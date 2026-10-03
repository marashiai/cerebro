"""Scripted loopback provider for protocol probes, never model-quality evidence.

The real native CLI owns tool dispatch and Cerebro owns all jobs and transports.
Only the remote provider responses and explicit transport faults are scripted.
"""

from collections import Counter
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import uuid


def tool_names(body):
    tools = body.get('tools', []) + [tool for item in body.get('input', [])
             if isinstance(item, dict) and item.get('type') == 'additional_tools'
             for tool in item['tools']]
    names = []
    for tool in tools:
        if tool.get('type') == 'namespace':
            separator = '__' if tool['name'].startswith('mcp__') else '.'
            names.extend(tool['name'] + separator + nested['name'] for nested in tool.get('tools', []))
        else:
            names.append(tool.get('name') or tool.get('function', {}).get('name'))
    return names


def outputs(body):
    return [item['output'] for item in body.get('input', []) if isinstance(item, dict)
            and item.get('type') in ('function_call_output', 'custom_tool_call_output')]


def response_events(name=None, arguments=None, text=None):
    identifier = uuid.uuid4().hex
    response = {'id': 'resp_' + identifier, 'object': 'response', 'created_at': int(time.time()),
                'model': 'scripted-protocol-provider', 'status': 'in_progress', 'output': []}
    custom = name == 'functions.exec'
    item = ({'type': 'custom_tool_call', 'id': 'ctc_' + identifier, 'call_id': 'call_' + identifier,
             'name': 'exec', 'namespace': 'functions', 'input': '', 'status': 'in_progress'} if custom else
            {'type': 'function_call', 'id': 'fc_' + identifier, 'call_id': 'call_' + identifier,
             'name': name, 'arguments': '', 'status': 'in_progress'} if name else
            {'type': 'message', 'id': 'msg_' + identifier, 'role': 'assistant',
             'status': 'in_progress', 'content': []})
    if name == 'mcp__cerebro__command':
        item.update(name='command', namespace='mcp__cerebro')
    events = [{'type': 'response.created', 'response': response},
              {'type': 'response.output_item.added', 'output_index': 0, 'item': item}]
    if name:
        value = arguments if custom else json.dumps(arguments)
        stem, field = ('custom_tool_call_input', 'input') if custom else ('function_call_arguments', 'arguments')
        events += [{'type': 'response.' + stem + '.delta', 'item_id': item['id'], 'output_index': 0, 'delta': value},
                   {'type': 'response.' + stem + '.done', 'item_id': item['id'], 'output_index': 0, field: value}]
        item = {**item, field: value, 'status': 'completed'}
    else:
        part = {'type': 'output_text', 'text': '', 'annotations': []}
        address = {'item_id': item['id'], 'output_index': 0, 'content_index': 0}
        events += [{'type': 'response.content_part.added', **address, 'part': part},
                   {'type': 'response.output_text.delta', **address, 'delta': text},
                   {'type': 'response.output_text.done', **address, 'text': text},
                   {'type': 'response.content_part.done', **address, 'part': {**part, 'text': text}}]
        item = {**item, 'status': 'completed', 'content': [{**part, 'text': text}]}
    events += [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
               {'type': 'response.completed', 'response': {**response, 'status': 'completed', 'output': [item],
                'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}}}]
    return events


def encode_events(events):
    return ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n'
                   for event in events).encode()


class Provider(AbstractContextManager):
    def __init__(self, directory, case):
        self.directory, self.case = directory, case
        self.records, self.errors = [], []
        self.counts = Counter()
        self.lock = threading.Lock()
        self.worker_started = threading.Event()
        self.worker_after_tool = threading.Event()
        self.parent_reconnected = threading.Event()
        self.release = threading.Event()
        self.job_id = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, mime, payload):
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.path == '/jev':
                        owner.record({'kind': 'jev_request', 'body': body})
                        if owner.case.endswith('-http'):
                            owner.record({'kind': 'injected_fault', 'fault': 'jev_http_429'})
                            self.reply(429, 'application/json', b'{"error":"PROBE_JEV_QUOTA"}')
                        else:
                            owner.record({'kind': 'injected_fault', 'fault': 'jev_malformed'})
                            self.reply(200, 'application/json', b'{"model":"scripted-probe","answers":{}}')
                        return
                    if self.path != '/v1/responses':
                        raise ValueError('unexpected provider path ' + self.path)
                    raw = json.dumps(body)
                    role = ('reconnected' if 'PROBE_RECONNECTED' in raw else
                            'parent' if 'PROBE_PARENT' in raw else
                            'review' if 'PROBE_REVIEW' in raw else
                            'worker' if 'PROBE_WORKER' in raw else 'unknown')
                    with owner.lock:
                        owner.counts[role] += 1
                        call = owner.counts[role]
                    owner.record({'kind': 'native_request', 'role': role, 'call': call,
                                  'tools': tool_names(body), 'body': body})
                    if role == 'unknown' or call > 8:
                        raise ValueError('unexpected scripted provider request role/count')
                    if role == 'worker' and owner.case.startswith('provider-'):
                        if owner.case == 'provider-interrupted-stream':
                            events = response_events(text='PROBE_PARTIAL_BEFORE_DISCONNECT')[:4]
                            owner.record({'kind': 'injected_fault', 'fault': 'interrupted_stream', 'events': events})
                            self.reply(200, 'text/event-stream', encode_events(events))
                        else:
                            code = ('rate_limit_exceeded' if owner.case == 'provider-token-limit'
                                    else 'insufficient_quota')
                            message = ('PROBE_TOKEN_LIMIT: tokens per minute exhausted' if code == 'rate_limit_exceeded'
                                       else 'PROBE_QUOTA_EXHAUSTED: provider quota exhausted')
                            fault = {'error': {'type': code, 'code': code, 'message': message}}
                            owner.record({'kind': 'injected_fault', 'fault': code, 'body': fault})
                            self.reply(429, 'application/json', json.dumps(fault).encode())
                        return
                    events = response_events(*owner.action(role, call, body))
                    owner.record({'kind': 'scripted_response', 'role': role, 'call': call, 'events': events})
                    self.reply(200, 'text/event-stream', encode_events(events))
                except (BrokenPipeError, ConnectionResetError):
                    owner.record({'kind': 'client_disconnected'})
                except Exception as error:
                    owner.errors.append(str(error))
                    owner.record({'kind': 'harness_error', 'error': str(error)})
                    self.send_error(500)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = 'http://127.0.0.1:' + str(self.server.server_port)

    def record(self, record):
        with self.lock:
            record = {'time': time.time(), **record}
            self.records.append(record)
            with (self.directory / 'provider.jsonl').open('a') as target:
                target.write(json.dumps(record) + '\n')

    def action(self, role, call, body):
        def invoke(name, arguments):
            names = tool_names(body)
            if 'functions.exec' in names and name not in names:
                return 'functions.exec', 'text(await tools.' + name + '(' + json.dumps(arguments) + '));', None
            return name, arguments, None

        if role in ('parent', 'review') and self.case == 'guarded-executors':
            if call == 1:
                return 'functions.exec', 'text(await tools.exec_command({cmd:"printf PROBE_FORBIDDEN_EXECUTED"}));', None
            if call == 2:
                return 'exec_command', {'cmd': 'printf PROBE_FORBIDDEN_EXECUTED'}, None
            if call == 3:
                return invoke('mcp__cerebro__command', {'argv': ['read', str(self.directory / 'repo'), 'proof.txt']})
            return None, None, 'PROBE_' + role.upper() + '_DONE: proof.txt:1 contains PROBE_PROOF.'
        if role == 'parent':
            if call == 1:
                return invoke('mcp__cerebro__command', {'argv': [
                    'execute', str(self.directory / 'repo'), '--prompt', 'PROBE_WORKER: write proof.txt.', '--no-watch']})
            return None, None, 'PROBE_PARENT_DONE'
        if role == 'reconnected':
            if call == 1:
                self.parent_reconnected.set()
                return invoke('mcp__cerebro__command', {'argv': ['wait', self.job_id]})
            return None, None, 'PROBE_RECONNECTED_DONE'
        if role == 'review':
            return None, None, 'PROBE_REVIEW_DONE: no material finding; user-work.txt:1 is preserved.'
        if call == 1:
            self.worker_started.set()
            if self.case == 'quiet-reconnect' and not self.release.wait(120):
                raise RuntimeError('quiet probe release was never delivered')
            return invoke('exec_command', {'cmd': "printf '%s' PROBE_PROOF > proof.txt"})
        if call == 2:
            self.worker_after_tool.set()
            if self.case == 'user-cancel':
                if not self.release.wait(120):
                    raise RuntimeError('cancel probe was never released')
                return invoke('exec_command', {'cmd': "printf '%s' UNWANTED_LATE_WRITE > late.txt"})
            if self.case == 'answer-resume':
                return None, None, 'QUESTION: May I append the authorized answer to the existing proof file?'
        if call == 3 and self.case == 'answer-resume':
            return invoke('exec_command', {'cmd': "test \"$(cat proof.txt)\" = PROBE_PROOF && printf '%s' ':ANSWER' >> proof.txt"})
        return None, None, 'PROBE_WORKER_DONE'

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
