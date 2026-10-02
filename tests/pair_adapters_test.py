"""Native admission/completion ordering and steering at the turn boundary."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib' / 'python'))
from pair_process import Claude, Codex
from pair_pi import Pi
from pi_fixture import seed_session


def reply(request, result):
    return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}


def completed(turn):
    return {'method': 'turn/completed', 'params': {'turn': {'id': turn, 'status': 'completed'}}}


def started(turn):
    return {'method': 'turn/started', 'params': {'turn': {'id': turn, 'status': 'inProgress'}}}


def setup():
    sent = []
    adapter = Codex(sent.append, '', '/tmp/cerebro-test', '', 'approved contract')
    adapter.start('initial task')
    assert sent[-1]['method'] == 'initialize'
    adapter.event(reply(sent[-1], {}))
    assert sent[-1]['method'] == 'thread/start'
    adapter.event(reply(sent[-1], {'thread': {'id': 'thread'}}))
    assert sent[-1]['method'] == 'turn/start'
    adapter.event(started('first'))
    return adapter, sent


with contextlib.redirect_stdout(io.StringIO()):
    # Acknowledgement is admission; it is never treated as task completion.
    adapter, sent = setup()
    assert not adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    assert adapter.event(completed('first'))

    # Native notifications can beat the RPC reply. Pending admission must hold
    # completion until the reply establishes which turn owns the request.
    adapter, sent = setup()
    assert not adapter.event(completed('first'))
    assert adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))

    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    adapter.steer('[supervisor] enforce the approved contract')
    admission = sent[-1]
    assert admission['method'] == 'turn/start'
    assert admission['params']['input'][0]['text'].startswith('[supervisor]')
    assert not adapter.event(completed('first'))
    assert adapter.event(reply(admission, {'turn': {'id': 'first'}}))

    # The same native method starts a fresh turn after completion. A delayed
    # prior-turn event cannot finish the newly admitted turn.
    adapter.steer('[user] continue within the contract')
    admission = sent[-1]
    assert not adapter.finished()
    assert not adapter.event(started('second'))
    assert not adapter.event(completed('first'))
    assert not adapter.event(reply(admission, {'turn': {'id': 'second'}}))
    assert adapter.event(completed('second'))

    adapter, sent = setup()
    first_admission = sent[-1]
    adapter.steer('[supervisor] steer across the completion boundary')
    second_admission = sent[-1]
    assert not adapter.event(started('second'))
    assert not adapter.event(reply(second_admission, {'turn': {'id': 'second'}}))
    assert not adapter.event(completed('second'))
    assert not adapter.event(reply(first_admission, {'turn': {'id': 'first'}}))
    assert adapter.event(completed('first'))

    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    assert not adapter.event({'method': 'item/started', 'params': {'item': {
        'id': 'background', 'type': 'commandExecution', 'status': 'inProgress'}}})
    assert not adapter.event(completed('first'))
    assert adapter.event({'method': 'item/completed', 'params': {'item': {
        'id': 'background', 'type': 'commandExecution', 'status': 'completed', 'exitCode': 0}}})

    adapter, sent = setup()
    try:
        adapter.event({'id': sent[-1]['id'], 'error': {'code': -32602, 'message': 'admission rejected'}})
    except RuntimeError as error:
        assert 'admission rejected' in str(error)
    else:
        raise AssertionError('RPC errors were treated as completion')

    adapter, sent = setup()
    assert not adapter.event({'id': 'host-request', 'method': 'item/commandExecution/requestApproval', 'params': {}})
    assert sent[-1]['error']['code'] == -32601

    # Claude's result handoff joins every submitted input; the first result
    # cannot discard another steering input already accepted on stdin.
    sent = []
    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    adapter.steer('[supervisor] enforce the approved contract')
    assert not adapter.event({'type': 'result', 'subtype': 'success', 'result': 'first result'})
    assert adapter.event({'type': 'result', 'subtype': 'success', 'result': 'steered result'})

with tempfile.TemporaryDirectory(prefix='cerebro-pi-admission-tests-') as temporary:
    session = Path(temporary) / 'conversation.jsonl'
    header = seed_session(session, temporary)

    fresh = Path(temporary) / 'fresh-empty.jsonl'
    fresh_header = seed_session(fresh, temporary)
    fresh.write_text('')
    sent, emitted = [], []
    adapter = Pi(sent.append, emitted.append, '')
    adapter.start('persist the first native user message')
    assert not adapter.event({'id': sent[-1]['id'], 'type': 'response', 'command': 'get_state', 'success': True,
                              'data': {'sessionFile': str(fresh), 'sessionId': fresh_header['id']}})
    assert emitted == [], 'an empty startup file was recorded as a durable conversation'
    fresh.write_text(json.dumps(fresh_header) + '\n' + json.dumps({
        'type': 'message', 'message': {'role': 'user', 'content': 'first native message'}}) + '\n')
    assert not adapter.event({'type': 'message_end', 'message': {'role': 'user', 'content': 'first native message'}})
    assert emitted[0] == {'type': 'session.started', 'session_id': str(fresh)}

    def pi_setup():
        sent, emitted = [], []
        adapter = Pi(sent.append, emitted.append, str(session))
        adapter.start('initial task')
        assert sent[-1]['type'] == 'get_state'
        assert not adapter.event({'id': sent[-1]['id'], 'type': 'response', 'command': 'get_state',
                                  'success': True, 'data': {'sessionFile': str(session), 'sessionId': header['id']}})
        assert emitted == [{'type': 'session.started', 'session_id': str(session)}]
        assert sent[-1]['type'] == 'prompt' and sent[-1]['streamingBehavior'] == 'steer'
        return adapter, sent, emitted

    def pi_reply(request, disposition='started'):
        return {'id': request['id'], 'type': 'response', 'command': 'prompt',
                'success': True, 'data': {'disposition': disposition}}

    adapter, sent, emitted = pi_setup()
    assert not adapter.event(pi_reply(sent[-1]))
    assert not adapter.event({'type': 'agent_start'})
    assert not adapter.event({'type': 'agent_end', 'messages': [], 'willRetry': True})
    assert not adapter.finished(), 'low-level agent_end abandoned automatic native work'
    assert adapter.event({'type': 'agent_settled'})

    adapter, sent, emitted = pi_setup()
    admission = sent[-1]
    assert not adapter.event({'type': 'agent_start'})
    assert not adapter.event({'type': 'agent_settled'})
    assert adapter.event(pi_reply(admission)), 'completion before the acknowledgement lost its ownership'

    adapter, sent, emitted = pi_setup()
    assert not adapter.event(pi_reply(sent[-1]))
    assert not adapter.event({'type': 'agent_start'})
    adapter.steer('[supervisor] enforce the approved contract')
    admission = sent[-1]
    assert admission['message'].startswith('[supervisor]') and admission['streamingBehavior'] == 'steer'
    assert not adapter.event({'type': 'agent_settled'})
    assert adapter.event(pi_reply(admission, 'queued'))
    adapter.steer('[user] continue after the native run settled')
    assert not adapter.finished()
    assert not adapter.event(pi_reply(sent[-1]))
    assert not adapter.event({'type': 'agent_settled'}), 'a buffered prior settlement finished a newly admitted run'
    assert not adapter.event({'type': 'agent_start'})
    assert adapter.event({'type': 'agent_settled'})

    adapter, sent, emitted = pi_setup()
    try:
        adapter.event(pi_reply(sent[-1], 'handled'))
    except RuntimeError as error:
        assert 'without starting a model run' in str(error)
    else:
        raise AssertionError('a consumed extension command was accepted as completed child work')

    adapter, sent, emitted = pi_setup()
    try:
        adapter.event({'id': sent[-1]['id'], 'type': 'response', 'command': 'prompt',
                       'success': False, 'error': 'admission rejected'})
    except RuntimeError as error:
        assert 'admission rejected' in str(error)
    else:
        raise AssertionError('Pi RPC rejection was treated as native completion')

    adapter = Pi(lambda _: None, lambda _: None, str(session))
    adapter.start('retain the original native conversation')
    other_session = Path(temporary) / 'another.jsonl'
    other_header = seed_session(other_session, temporary)
    try:
        adapter.event({'id': 'cerebro-1', 'type': 'response', 'command': 'get_state', 'success': True,
                       'data': {'sessionFile': str(other_session), 'sessionId': other_header['id']}})
    except RuntimeError as error:
        assert 'another session file' in str(error)
    else:
        raise AssertionError('Pi silently resumed another native conversation')

print('all checks passed')
