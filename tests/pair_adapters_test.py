"""Native admission/completion ordering and steering at the turn boundary."""

import contextlib
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib' / 'python'))
from pair_process import Claude, Codex


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
    adapter.steer('[observer] enforce the approved contract')
    admission = sent[-1]
    assert admission['method'] == 'turn/start'
    assert admission['params']['input'][0]['text'].startswith('[observer]')
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
    adapter.steer('[observer] steer across the completion boundary')
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
    adapter.steer('[observer] enforce the approved contract')
    assert not adapter.event({'type': 'result', 'subtype': 'success', 'result': 'first result'})
    assert adapter.event({'type': 'result', 'subtype': 'success', 'result': 'steered result'})

print('all checks passed')
