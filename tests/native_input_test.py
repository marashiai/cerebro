"""Native input capture at real offline frontend boundaries; no provider inference."""

import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib/python'))
from codex_launch import native_hook_options, toml
from user_input import record_text, snapshot


class AppServer:
    def __init__(self, executable, cwd, env, options):
        self.process = subprocess.Popen([executable, 'app-server', *options], cwd=cwd, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
        self.events = queue.Queue()
        self.next_id = 0
        def reader():
            for line in self.process.stdout:
                self.events.put(json.loads(line))
            self.events.put(None)
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        self.request('initialize', {'clientInfo': {'name': 'cerebro-input-offline-test', 'version': '1'},
                                    'capabilities': {'experimentalApi': True}})
        self.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def event(self):
        value = self.events.get(timeout=15)
        if value is None:
            raise RuntimeError('offline app-server exited before reply')
        return value

    def request(self, method, params):
        self.next_id += 1
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params})
        while True:
            event = self.event()
            if event.get('id') == self.next_id:
                if 'error' in event:
                    raise RuntimeError(str(event['error']))
                return event['result']

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()


class NativeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cerebro-native-input-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / 'home/sessions/fixture'
        self.session.mkdir(parents=True)
        self.metadata = self.session / 'metadata.json'
        self.metadata.write_text('{"role":"supervisor","backend":"codex"}')
        (self.root / 'native').mkdir()
        self.env = {'HOME': str(self.root), 'PATH': os.environ['PATH'],
                    'CODEX_HOME': str(self.root / 'native')}
        self.codex = shutil.which('codex')

    def server(self, options):
        server = AppServer(self.codex, str(self.root), self.env, options)
        self.addCleanup(server.close)
        return server

    def hook_options(self):
        with patch.dict(os.environ, self.env, clear=True):
            return native_hook_options(self.codex, str(self.root), self.session)

    def test_native_exact_trust_preserves_existing_user_hooks_and_state(self):
        if not self.codex:
            self.skipTest('installed Codex unavailable')
        config = self.root / 'native/config.toml'
        definition = 'hooks.UserPromptSubmit=' + toml([
            {'hooks': [{'type': 'command', 'command': '/usr/bin/true', 'timeout': 7}]}])
        config.write_text(definition + '\n')
        first = self.server([])
        old = first.request('hooks/list', {'cwds': [str(self.root)]})['data'][0]['hooks'][0]
        state = {old['key']: {'trusted_hash': old['currentHash']}, 'unrelated': {'enabled': False}}
        original = definition + '\nhooks.state=' + toml(state) + '\n'
        config.write_text(original)
        combined = self.server(self.hook_options())
        hooks = combined.request('hooks/list', {'cwds': [str(self.root)]})['data'][0]['hooks']
        self.assertEqual(len(hooks), 2)
        preserved = next(item for item in hooks if item['source'] == 'user')
        owned = next(item for item in hooks if item['source'] == 'sessionFlags')
        self.assertEqual((preserved['key'], preserved['currentHash'], preserved['trustStatus']),
                         (old['key'], old['currentHash'], 'trusted'))
        self.assertEqual(owned['trustStatus'], 'trusted')
        effective = combined.request('config/read', {'cwd': str(self.root), 'includeLayers': False})
        self.assertEqual(effective['config']['hooks']['state']['unrelated'], {'enabled': False})
        self.assertEqual(config.read_text(), original)

    def local_turn(self, blocked=False):
        if not self.codex:
            self.skipTest('installed Codex unavailable')
        prompt = 'Busy claims return None; invalid completion returns False.\n\n'
        hits = []
        session = self.session
        class Reject(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(snapshot(session)[-1]['content'])
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"offline test refuses inference","type":"invalid_request_error"}}')
            def log_message(self, *args):
                pass
        endpoint = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Reject)
        threading.Thread(target=endpoint.serve_forever, daemon=True).start()
        self.addCleanup(endpoint.server_close)
        self.addCleanup(endpoint.shutdown)
        provider = {'name': 'Cerebro offline rejection test',
                    'base_url': 'http://127.0.0.1:' + str(endpoint.server_port) + '/v1',
                    'wire_api': 'responses', 'requires_openai_auth': False,
                    'request_max_retries': 0, 'stream_max_retries': 0}
        options = self.hook_options() + ['-c', 'model_provider="cerebro-input-test"',
                    '-c', 'model_providers.cerebro-input-test=' + toml(provider),
                    '-c', 'model="fixture-model"']
        if blocked:
            record_text(session, 'Previous request', source='fixture')
            self.metadata.write_text('{"role":"supervisor","backend":"codex","foreign_session_id":"other-thread"}')
        server = self.server(options)
        native_id = server.request('thread/start', {'cwd': str(self.root), 'approvalPolicy': 'never',
                                                    'sandbox': 'danger-full-access'})['thread']['id']
        server.request('turn/start', {'threadId': native_id, 'input': [{'type': 'text', 'text': prompt}]})
        while True:
            event = server.event()
            if event.get('method') == 'turn/completed':
                break
        if blocked:
            self.assertEqual(hits, [])
            self.assertEqual(snapshot(session)[-1]['content'], [{'type': 'text', 'text': 'Previous request'}])
        else:
            self.assertEqual(event['params']['turn']['status'], 'failed')
            self.assertEqual(hits, [[{'type': 'text', 'text': prompt}]])
            self.assertEqual(snapshot(session)[0]['content'], [{'type': 'text', 'text': prompt}])
            self.assertEqual(json.loads(self.metadata.read_text())['foreign_session_id'], native_id)

    def test_native_codex_capture_is_durable_before_first_provider_request(self):
        self.local_turn()

    def test_native_codex_failed_capture_blocks_before_provider_request(self):
        self.local_turn(blocked=True)

    def test_native_pi_input_precedes_auth_failure_and_native_file_creation(self):
        executable = shutil.which('pi')
        if not executable:
            self.skipTest('installed Pi unavailable')
        self.metadata.write_text('{"role":"supervisor","backend":"pi"}')
        config = self.root / 'tools.json'
        config.write_text(json.dumps({'mcpServers': {'cerebro': {'command': str(ROOT / 'bin/cerebro'),
            'args': ['tools', 'supervisor'], 'env': {'CEREBRO_HOME': str(self.root / 'home'),
            'CEREBRO_SESSION_DIR': str(self.session), 'CEREBRO_SESSION_ID': 'fixture',
            'CEREBRO_BACKEND': 'pi', 'CEREBRO_JEV_ENABLED': '0'}}}}))
        prompt = 'Retain the exact user task.\n\n'
        result = subprocess.run([executable, '--offline', '--print', '--no-extensions',
            '-e', str(ROOT / 'lib/payloads/pi/cerebro.ts'), '--cerebro-role', 'supervisor',
            '--cerebro-mcp-config', str(config), '--cerebro-bind', str(self.session),
            '--session-id', '00000000-0000-4000-8000-000000000001', prompt],
            cwd=self.root, env={**self.env, 'PI_OFFLINE': '1', 'PI_CODING_AGENT_DIR': str(self.root / 'pi')},
            text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No API key', result.stderr)
        self.assertEqual(snapshot(self.session)[0]['content'], [{'type': 'text', 'text': prompt}])
        native = json.loads(self.metadata.read_text())['foreign_session_id']
        self.assertFalse(Path(native).exists(), 'capture required the not-yet-created native transcript')


if __name__ == '__main__':
    unittest.main()
