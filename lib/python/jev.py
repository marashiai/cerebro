"""Jev's typed HTTP contract, without a second client runtime or SDK."""

import json
import math
from pathlib import Path
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = 'https://api.typesafe.ai/v1/systemone'


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
        self.questions = json.loads((Path(__file__).resolve().parent.parent /
                                     'payloads' / 'jev' / 'questions.json').read_text())

    def classify(self, state):
        questions = {**self.questions, 'evidence': {
            'type': 'choice',
            'instructions': 'Select the event ID containing the strongest concrete evidence '
                            'for a possible scope deviation. Select none for in-scope work '
                            'or when no supplied event supports a concern.',
            'criteria': {'none': 'No event provides concrete evidence of a scope deviation',
                         **{event['id']: 'The event in `events` with ID ' + event['id']
                            for event in state['events']}},
        }}
        body = json.dumps({'model': self.model, 'state': state, 'questions': questions}).encode()
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
        threading.Thread(target=fetch, daemon=True).start()
        try:
            raw, error = completed.get(timeout=self.timeout)
        except queue.Empty:
            raise RuntimeError('Jev request exceeded its total deadline') from None
        if error:
            raise error
        if len(raw) > 256 * 1024:
            raise ValueError('Jev response exceeded 256 KiB')
        try:
            result = json.loads(raw)
            model, answers = result['model'], result['answers']
            if not isinstance(model, str) or not model or set(answers) != set(questions):
                raise ValueError()
            for name, question in questions.items():
                answer = answers[name]
                probabilities = answer['probabilities']
                confidence = answer['confidence']
                if (answer['type'] != 'choice' or answer['choice'] not in question['criteria']
                        or set(probabilities) != set(question['criteria'])
                        or not number(confidence) or not 0 <= confidence <= 1
                        or any(not number(value) or not 0 <= value <= 1
                               for value in probabilities.values())
                        or not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-5)
                        or probabilities[answer['choice']] < max(probabilities.values())):
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Jev returned an invalid typed classification') from None
        return {'model': model, 'scope': answers['scope']['choice'],
                'confidence': answers['scope']['confidence'],
                'reason': answers['reason']['choice'], 'evidence_id': answers['evidence']['choice']}

    def fetch(self, request):
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(256 * 1024 + 1)
        except urllib.error.HTTPError as error:
            raise RuntimeError('Jev HTTP ' + str(error.code)) from None
        except (OSError, urllib.error.URLError):
            raise RuntimeError('Jev request failed or timed out') from None
        return raw


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args):
        return None
