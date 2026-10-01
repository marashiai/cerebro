"""Deterministic native OpenCode V2 transport for CLI contract tests.

This stands in for OpenCode, not the Cerebro pump: tests use the production
session, authentication, SSE and steering paths. Runtime/provider verification
uses the installed CLI separately.
"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time


def main():
    directory = Path(sys.argv[1])
    config = json.loads((directory / 'fixture.json').read_text())
    arguments = sys.argv[2:]
    if arguments == ['--version']:
        print(config.get('version', '2.0.19'))
        return
    if arguments[:1] == ['api']:
        if 'POST' not in arguments or '/api/rpc/cerebro/ready' not in arguments:
            raise SystemExit('unexpected fixture API command')
        if json.loads(arguments[arguments.index('--data') + 1]) != {'input': {}}:
            raise SystemExit('unexpected role-gate RPC payload')
        print(json.dumps({'output': config.get('ready', True)}))
        return
    if arguments[:1] != ['serve']:
        launch_log = config.get('launch_log')
        if not launch_log:
            raise SystemExit('unexpected fixture CLI command: ' + repr(arguments))
        with open(launch_log, 'a') as log:
            log.write(json.dumps(arguments) + '\n')
        context_log = config.get('launch_context_log')
        if context_log:
            with open(context_log, 'a') as log:
                log.write(json.dumps({'argv': arguments, 'pid': os.getpid(), 'cwd': os.getcwd(),
                                      'session_id': os.environ.get('CEREBRO_SESSION_ID'),
                                      'session_dir': os.environ.get('CEREBRO_SESSION_DIR'),
                                      'config': json.loads(os.environ.get('OPENCODE_CONFIG_CONTENT') or '{}')}) + '\n')
        if config.get('interactive'):
            print('READY', flush=True)
            for line in sys.stdin:
                print('ACK ' + line.strip(), flush=True)
        return
    port = int(arguments[arguments.index('--port') + 1])
    sid = config.get('sid', 'TEST-SESSION')
    events = queue.Queue()
    request_lock = threading.Lock()
    token = base64.b64encode(('opencode:' + os.environ['OPENCODE_PASSWORD']).encode()).decode()
    stalled = False
    stall_state = os.environ.get('FAKE_STALL_STATE')
    if stall_state and not Path(stall_state).exists():
        Path(stall_state).touch()
        stalled = True

    def record(path, payload):
        request_log = config.get('request_log') or os.environ.get('IMPROVE_STUB_LOG')
        if request_log:
            with request_lock, open(request_log, 'a') as log:
                log.write(json.dumps({'path': path, 'payload': payload,
                                     'isolated': os.environ.get('PLAYWRIGHT_MCP_ISOLATED')}) + '\n')

    def enqueue(kind, session_id, **data):
        data['sessionID'] = session_id
        events.put({'type': kind, 'data': data})

    def execute(session_id, text):
        count = config.get('count_file')
        if count:
            with open(count, 'a') as output:
                output.write('x')
        prompt_capture = config.get('prompt_capture') or os.environ.get('PROMPT_CAPTURE')
        if prompt_capture:
            Path(prompt_capture).write_text(text)
        hook = config.get('hook')
        if hook:
            subprocess.run(['bash', hook], input=text, text=True, check=True)
        if stalled:
            return
        time.sleep(config.get('delay', 0))
        enqueue('session.execution.started', session_id)
        enqueue('session.step.started', session_id, assistantMessageID='assistant')
        mode = config.get('mode', 'ok')
        if mode == 'background':
            enqueue('session.tool.input.started', session_id, id='background', name='shell')
            enqueue('session.tool.called', session_id, id='background', input={'command': 'fixture child', 'background': True})
            enqueue('session.tool.success', session_id, id='background', content=[],
                    metadata={'status': 'running', 'shellID': 'shell-background'})
            enqueue('session.execution.succeeded', session_id)
            time.sleep(0.3)
            enqueue('session.inbox.enqueued', session_id, inboxID='inbox-background', item={
                'type': 'synthetic', 'payload': {'metadata': {'source': 'shell', 'shellID': 'shell-background'}}})
            enqueue('session.inbox.delivered', session_id, inboxID='inbox-background')
            enqueue('session.execution.started', session_id)
            response = 'BACKGROUND_JOINED'
        elif mode == 'improve':
            mode = os.environ.get('IMPROVE_STUB_MODE', 'valid')
            meta = 'META-LOOP' in text
            response = ('## 1. Meta finding\nMETA CLIMB: ISSUES FOUND' if meta
                        else '1. Fast finding\nHILL CLIMB: ISSUES FOUND')
            if mode == 'malformed':
                response = '## 1. Finding\nHILL CLIMB: MAYBE'
            elif mode == 'meta-malformed' and meta:
                response = '## 1. Meta finding\nMETA CLIMB: MAYBE'
        else:
            response = config.get('text', 'ok')
        if mode == 'concurrent':
            token_match = re.search(r'TOKEN=([A-Z]+)', text)
            response = token_match[1]
        if mode != 'empty':
            for _ in range(config.get('text_count', 1)):
                enqueue('session.text.ended', session_id, text=response, assistantMessageID='assistant')
        if mode in ('failure', 'worked-failure'):
            if mode == 'worked-failure':
                enqueue('session.tool.input.started', session_id, id='tool', name='shell')
                enqueue('session.tool.called', session_id, id='tool', input={'command': 'git commit'})
                enqueue('session.tool.success', session_id, id='tool', content=[{'type': 'text', 'text': 'done'}])
            enqueue('session.execution.failed', session_id, error={'message': 'fixture failure'})
        else:
            enqueue('session.execution.succeeded', session_id)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body=None):
            encoded = json.dumps(body).encode() if body is not None else b''
            self.send_response(code)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def authenticated(self):
            if self.headers.get('authorization') == 'Basic ' + token:
                return True
            self.send(401)
            return False

        def do_GET(self):
            if not self.authenticated():
                return
            if self.path == '/api/info':
                self.send(200, {'data': {'version': '2.0.19'}})
            elif self.path == '/api/event':
                self.send_response(200)
                self.send_header('content-type', 'text/event-stream')
                self.end_headers()
                self.wfile.flush()
                try:
                    while True:
                        event = events.get()
                        self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send(404)

        def do_POST(self):
            if not self.authenticated():
                return
            payload = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))))
            record(self.path, payload)
            if self.path == '/api/rpc/cerebro/ready':
                if payload != {'input': {}}:
                    self.send(400)
                else:
                    self.send(200, {'output': config.get('ready', True)})
                return
            if self.path == '/api/session':
                if not isinstance(payload.get('location', {}).get('directory'), str):
                    self.send(400)
                    return
                self.send(200, {'data': {'id': sid}})
                return
            match = re.fullmatch(r'/api/session/([^/]+)/(agent|model|prompt|interrupt)', self.path)
            if not match:
                self.send(404)
                return
            session_id, action = match.groups()
            if action == 'agent' and payload != {'agent': 'general'}:
                self.send(400, {'error': 'expected native general agent'})
            elif action == 'model' and not {'providerID', 'id'} <= payload.get('model', {}).keys():
                self.send(400)
            elif action == 'prompt':
                if not isinstance(payload.get('text'), str):
                    self.send(400)
                elif config.get('mode') == 'reject' or os.environ.get('FAKE_REJECT_PROMPT'):
                    self.send(400, {'error': 'prompt rejected'})
                else:
                    self.send(204)
                    threading.Thread(target=execute, args=(session_id, payload['text']), daemon=True).start()
            else:
                self.send(204)

    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
