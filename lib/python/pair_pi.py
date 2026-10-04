"""Native Pi RPC acknowledgements and settled-run completion."""

from pathlib import Path

from pi_launch import validate_session


class Pi:
    def __init__(self, send, emit, resume):
        self.send = send
        self.emit = emit
        self.resume = resume
        self.session_file = None
        self.session_id = None
        self.bound = False
        self.next_id = 0
        self.requests = {}
        self.pending = []
        self.busy = {}
        self.active_turns = set()
        self.settled = False
        self.run_number = 0
        self.starting = False

    def request(self, kind, **params):
        self.next_id += 1
        request_id = 'cerebro-' + str(self.next_id)
        self.requests[request_id] = (kind, self.run_number)
        self.send({'id': request_id, 'type': kind, **params})

    def start(self, prompt):
        self.pending.append(prompt)
        self.request('get_state')

    def steer(self, text):
        self.settled = False
        self.pending.append(text)
        self.flush()

    def flush(self):
        if self.session_file and self.pending and not any(kind == 'prompt' for kind, _ in self.requests.values()):
            text = '\n\n'.join(self.pending)
            self.pending.clear()
            self.request('prompt', message=text, streamingBehavior='steer')

    def bind(self):
        if self.bound or not self.session_file:
            return
        session = Path(self.session_file)
        if not self.resume and (not session.is_file() or not session.stat().st_size):
            return
        if validate_session(self.session_file) != self.session_id:
            raise RuntimeError('Pi session identity changed')
        self.emit({'type': 'session.started', 'session_id': self.session_file})
        self.bound = True

    def event(self, event):
        kind = event.get('type')
        if kind == 'response':
            purpose, sent_run = self.requests.pop(event.get('id'), (None, None))
            if not event.get('success'):
                raise RuntimeError('Pi RPC failed: ' + str(event.get('error', purpose)))
            data = event.get('data') or {}
            if purpose == 'get_state':
                self.session_file = data['sessionFile']
                self.session_id = data['sessionId']
                if not Path(self.session_file).is_absolute():
                    raise RuntimeError('Pi returned a relative session file')
                if self.resume and Path(self.session_file).resolve() != Path(self.resume).resolve():
                    raise RuntimeError('Pi resumed another session file')
                self.bind()
            elif purpose == 'prompt':
                disposition = data.get('disposition')
                if disposition == 'started' and self.run_number == sent_run:
                    # A previous run's settlement may still be buffered when
                    # steering starts the next run; its ACK precedes agent_start.
                    self.active_turns.add('run')
                    self.settled = False
                    self.starting = True
                elif disposition == 'handled':
                    raise RuntimeError('Pi handled a child instruction without starting a model run')
                elif disposition not in ('started', 'queued'):
                    raise RuntimeError('unknown Pi prompt disposition: ' + str(disposition))
            self.flush()
            return self.ended()
        if kind == 'agent_start':
            self.run_number += 1
            self.starting = False
            self.settled = False
            self.active_turns.add('run')
        elif kind == 'tool_execution_start':
            args = event.get('args') or {}
            self.busy[event['toolCallId']] = {'tool': event.get('toolName'), 'command': args.get('command')}
        elif kind == 'tool_execution_end':
            self.busy.pop(event['toolCallId'], None)
        elif kind == 'agent_settled' and not self.starting:
            self.active_turns.clear()
            self.settled = True
        if kind in ('message_end', 'agent_settled'):
            self.bind()
        self.emit(event)
        return self.ended()

    def ended(self):
        return self.settled and not self.starting and not self.active_turns and not self.pending and not self.requests
