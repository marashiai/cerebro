"""Jev's typed HTTP contract, without a second client runtime or SDK."""

from datetime import datetime, timezone
import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
RESPONSE_LIMIT = 256 * 1024


def write_trace(path, record):
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as log:
        os.fchmod(log.fileno(), 0o600)
        log.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), **record},
                             ensure_ascii=False) + '\n')


class Jev:
    def __init__(self, key, model='jev-latest', endpoint=ENDPOINT, timeout=15):
        if not key.strip() or not model.strip():
            raise ValueError('Jev requires a nonempty API key and model')
        if any(not 32 <= ord(char) <= 126 for char in key):
            raise ValueError('Jev API key must be printable ASCII on one line')
        if not number(timeout) or timeout <= 0:
            raise ValueError('Jev timeout must be positive')
        url = urllib.parse.urlsplit(endpoint)
        if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in
                ('localhost', '127.0.0.1', '::1'))) or not url.hostname:
            raise ValueError('Jev endpoint must use HTTPS or loopback HTTP')
        if url.username or url.password or url.query or url.fragment:
            raise ValueError('Jev endpoint must not contain credentials, a query or a fragment')
        self.key, self.model, self.endpoint, self.timeout = key, model, endpoint, timeout

    def evaluate(self, state, questions, log_path):
        identifier = uuid.uuid4().hex
        payload = {'model': self.model, 'state': state, 'questions': questions}
        # Persist the input before starting HTTP so interrupted calls remain visible.
        # Request headers, including the credential, never enter the trace.
        write_trace(log_path, {'type': 'request', 'request_id': identifier,
                               'endpoint': self.endpoint, 'payload': payload})
        body = json.dumps(payload).encode()
        request = urllib.request.Request(self.endpoint, data=body, headers={
            'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        completed = queue.Queue()

        def fetch():
            try:
                completed.put((self.fetch(request), None))
            except Exception as error:
                completed.put((None, error))

        # urllib's timeout bounds socket inactivity, not elapsed request time.
        # The owning child pump exits on failure; its daemon cannot retain it.
        started = time.monotonic()
        response = {'type': 'response', 'request_id': identifier}
        try:
            threading.Thread(target=fetch, daemon=True).start()
            try:
                fetched, error = completed.get(timeout=self.timeout)
            except queue.Empty:
                raise RuntimeError('Jev request exceeded its total deadline') from None
            if error:
                raise error
            status, raw = fetched
            response.update(http_status=status, body=raw[:RESPONSE_LIMIT].decode('utf-8', errors='replace'),
                            truncated=len(raw) > RESPONSE_LIMIT)
            if not 200 <= status < 300:
                raise RuntimeError('Jev HTTP ' + str(status))
            if len(raw) > RESPONSE_LIMIT:
                raise ValueError('Jev response exceeded 256 KiB')
            result = self.validate(raw, questions)
            return {**result, 'request_id': identifier}
        except Exception as error:
            response['error'] = str(error)
            raise
        finally:
            response['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            write_trace(log_path, response)

    @staticmethod
    def validate(raw, questions):
        question_name = 'response'
        try:
            result = json.loads(raw)
            model, answers = result['model'], result['answers']
            if not isinstance(model, str) or not model or set(answers) != set(questions):
                raise ValueError()
            for name, question in questions.items():
                question_name = name
                answer = answers[name]
                probabilities = answer['probabilities']
                confidence = answer['confidence']
                # Jev rounds each probability to two decimals; the total can
                # differ from one by up to half a rounding unit per option.
                if (answer['type'] != 'choice' or answer['choice'] not in question['criteria']
                        or set(probabilities) != set(question['criteria'])
                        or not number(confidence) or not 0 <= confidence <= 1
                        or any(not number(value) or not 0 <= value <= 1
                               for value in probabilities.values())
                        or not math.isclose(sum(probabilities.values()), 1,
                                            abs_tol=0.005 * len(probabilities) + 1e-9)
                        or probabilities[answer['choice']] < max(probabilities.values())):
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Jev returned an invalid typed classification (' + question_name + ')') from None
        return result

    def fetch(self, request):
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                return response.status, response.read(RESPONSE_LIMIT + 1)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(RESPONSE_LIMIT + 1)
        except (OSError, urllib.error.URLError):
            raise RuntimeError('Jev request failed or timed out') from None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args):
        return None
