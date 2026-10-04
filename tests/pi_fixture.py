"""Deterministic Pi native JSON RPC process for CLI contract tests.

The fixture implements the installed 0.99.2 RPC/event boundary; Cerebro's
production launcher, adapter, parser, worktree and steering code stay in use.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


def seed_session(path, cwd):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    header = {'type': 'session', 'version': 3, 'id': str(uuid.uuid4()),
              'timestamp': now, 'cwd': str(Path(cwd).resolve())}
    message = {'type': 'message', 'id': 'seed', 'parentId': None, 'timestamp': now,
               'message': {'role': 'user', 'content': 'existing conversation', 'timestamp': 1}}
    path.write_text(json.dumps(header) + '\n' + json.dumps(message) + '\n')
    return header


def main():
    if sys.argv[1:2] == ['--seed']:
        seed_session(sys.argv[2], sys.argv[3])
        return
    directory = Path(sys.argv[1])
    config = json.loads((directory / 'fixture.json').read_text())
    arguments = sys.argv[2:]
    if arguments == ['--version']:
        print(config.get('version', '0.99.2'))
        return
    record_lock = threading.Lock()
    output_lock = threading.Lock()

    def record(payload):
        path = config.get('request_log') or os.environ.get('IMPROVE_STUB_LOG')
        if path:
            with record_lock, open(path, 'a') as log:
                log.write(json.dumps({**payload, 'cwd': os.getcwd(),
                                      'isolated': os.environ.get('PLAYWRIGHT_MCP_ISOLATED')}) + '\n')

    role = arguments[arguments.index('--cerebro-role') + 1] if '--cerebro-role' in arguments else os.environ.get('CEREBRO_ROLE')
    record({'argv': arguments, 'role': role,
            'child_role': os.environ.get('CEREBRO_CHILD_ROLE')})
    if '--mode' not in arguments or arguments[arguments.index('--mode') + 1] != 'rpc':
        if not config.get('launch_log'):
            raise SystemExit('unexpected Pi fixture CLI command: ' + repr(arguments))
        with open(config['launch_log'], 'a') as log:
            log.write(json.dumps(arguments) + '\n')
        if config.get('launch_context_log'):
            with open(config['launch_context_log'], 'a') as log:
                log.write(json.dumps({'argv': arguments, 'pid': os.getpid(), 'cwd': os.getcwd(),
                                      'session_id': os.environ.get('CEREBRO_SESSION_ID'),
                                      'session_dir': os.environ.get('CEREBRO_SESSION_DIR')}) + '\n')
        if config.get('interactive'):
            print('READY', flush=True)
            for line in sys.stdin:
                print('ACK ' + line.strip(), flush=True)
        return

    if '--session' in arguments:
        session_file = Path(arguments[arguments.index('--session') + 1]).resolve()
    else:
        sessions = directory / 'native-sessions'
        sessions.mkdir(exist_ok=True)
        session_file = sessions / (str(uuid.uuid4()) + '.jsonl')
    if session_file.is_file() and session_file.stat().st_size:
        header = json.loads(session_file.read_text().splitlines()[0])
    else:
        header = {'type': 'session', 'version': 3, 'id': str(uuid.uuid4()),
                  'timestamp': datetime.now(timezone.utc).isoformat(), 'cwd': os.getcwd()}
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.touch()
    stalled = False
    stall_state = os.environ.get('FAKE_STALL_STATE')
    if stall_state and not Path(stall_state).exists():
        Path(stall_state).touch()
        stalled = True
    tasks_lock = threading.Lock()
    task_count = 0
    tasks = queue.Queue()

    def emit(event):
        with output_lock:
            print(json.dumps(event), flush=True)

    def response(command, data=None, error=''):
        emit({'type': 'response', 'id': command['id'], 'command': command['type'],
              'success': not error, **({'error': error} if error else {'data': data or {}})})

    def persist(message):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with record_lock:
            if not session_file.stat().st_size:
                session_file.write_text(json.dumps(header) + '\n')
            entry = {'type': 'message', 'id': str(uuid.uuid4())[:8], 'parentId': None,
                     'timestamp': datetime.now(timezone.utc).isoformat(), 'message': message}
            with session_file.open('a') as log:
                log.write(json.dumps(entry) + '\n')

    def execute(text):
        nonlocal task_count
        try:
            count = config.get('count_file')
            if count:
                with open(count, 'a') as output:
                    output.write('x')
            prompt_capture = config.get('prompt_capture') or os.environ.get('PROMPT_CAPTURE')
            if prompt_capture:
                Path(prompt_capture).write_text(text)
            user = {'role': 'user', 'content': text, 'timestamp': 1}
            persist(user)
            emit({'type': 'agent_start'})
            emit({'type': 'turn_start'})
            emit({'type': 'message_end', 'message': user})
            hook = config.get('hook')
            if hook:
                subprocess.run(['bash', hook], input=text, text=True, check=True)
            if stalled:
                return
            time.sleep(config.get('delay', 0))
            mode = config.get('mode', 'ok')
            if os.environ.get('TASK_FIXTURE_CONFIG'):
                roles = json.loads(Path(os.environ['TASK_FIXTURE_CONFIG']).read_text())
                mode = roles.get(os.environ['CEREBRO_CHILD_ROLE'], {}).get('native_mode', mode)
            if mode == 'improve':
                mode = os.environ.get('IMPROVE_STUB_MODE', 'valid')
                meta = 'META-LOOP' in text
                result = ('## 1. Meta finding\nMETA CLIMB: ISSUES FOUND' if meta
                          else '1. Fast finding\nHILL CLIMB: ISSUES FOUND')
                if mode == 'malformed':
                    result = '## 1. Finding\nHILL CLIMB: MAYBE'
                elif mode == 'meta-malformed' and meta:
                    result = '## 1. Meta finding\nMETA CLIMB: MAYBE'
            else:
                result = config.get('text', 'ok')
            if os.environ.get('TASK_FIXTURE_CONFIG'):
                from task_fixture import result as task_result
                result = task_result(text, os.environ['CEREBRO_CHILD_ROLE'])
            if mode == 'concurrent':
                result = re.search(r'TOKEN=([A-Z]+)', text)[1]
            if mode == 'background':
                emit({'type': 'agent_end', 'messages': [], 'willRetry': True})
                emit({'type': 'auto_retry_start', 'attempt': 1, 'maxAttempts': 1,
                      'delayMs': 300, 'errorMessage': 'fixture automatic continuation'})
                time.sleep(0.3)
                emit({'type': 'agent_start'})
                result = 'AUTOMATIC_WORK_JOINED'
            if mode == 'abandoned':
                emit({'type': 'tool_execution_start', 'toolCallId': 'check-command', 'toolName': 'bash',
                      'args': {'command': 'python3 hanging_check.py'}})
            if mode == 'worked-failure':
                emit({'type': 'tool_execution_start', 'toolCallId': 'tool', 'toolName': 'bash',
                      'args': {'command': 'git commit'}})
                emit({'type': 'tool_execution_end', 'toolCallId': 'tool', 'toolName': 'bash',
                      'result': {'content': [{'type': 'text', 'text': 'done'}]}, 'isError': False})
            message = {'role': 'assistant', 'content': [] if mode == 'empty' else
                       [{'type': 'text', 'text': result}], 'timestamp': 2,
                       'stopReason': 'error' if mode in ('failure', 'worked-failure') else 'stop'}
            if message['stopReason'] == 'error':
                message['errorMessage'] = 'fixture failure'
            for _ in range(config.get('text_count', 0)):
                emit({'type': 'message_update', 'assistantMessageEvent': {
                    'type': 'text_delta', 'contentIndex': 0, 'delta': result}})
            persist(message)
            emit({'type': 'message_end', 'message': message})
            emit({'type': 'turn_end', 'message': message, 'toolResults': []})
            emit({'type': 'agent_end', 'messages': [message], 'willRetry': False})
        finally:
            with tasks_lock:
                task_count -= 1
                if task_count == 0 and not stalled:
                    emit({'type': 'agent_settled'})

    def work():
        while True:
            execute(tasks.get())

    threading.Thread(target=work, daemon=True).start()
    for line in sys.stdin:
        command = json.loads(line)
        record(command)
        kind = command['type']
        if kind == 'get_state':
            response(command, {'sessionFile': str(session_file), 'sessionId': header['id'],
                               'isStreaming': task_count > 0, 'isCompacting': False,
                               'pendingMessageCount': max(0, task_count - 1)})
        elif kind == 'prompt':
            if config.get('mode') == 'reject' or os.environ.get('FAKE_REJECT_PROMPT'):
                response(command, error='fixture prompt rejected')
            else:
                with tasks_lock:
                    disposition = 'queued' if task_count else 'started'
                    task_count += 1
                response(command, {'disposition': disposition})
                tasks.put(command['message'])
        elif kind == 'abort':
            response(command)
        else:
            raise SystemExit('unexpected Pi fixture RPC command: ' + str(kind))


if __name__ == '__main__':
    main()
