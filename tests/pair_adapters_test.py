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

    # An active turn is steered in place; its completion waits for the steer
    # acknowledgement so the input's turn is known.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    adapter.steer('[supervisor] enforce the approved contract')
    steer = sent[-1]
    assert steer['method'] == 'turn/steer' and steer['params']['expectedTurnId'] == 'first'
    assert steer['params']['input'][0]['text'].startswith('[supervisor]')
    assert not adapter.event(completed('first')), 'completion beat the steer acknowledgement'
    assert adapter.event(reply(steer, {'turnId': 'first'}))

    # Idle input starts a fresh turn. A delayed prior-turn event cannot finish it.
    adapter.steer('[user] continue within the contract')
    admission = sent[-1]
    assert admission['method'] == 'turn/start'
    assert not adapter.ended()
    assert not adapter.event(started('second'))
    assert not adapter.event(completed('first'))
    assert not adapter.event(reply(admission, {'turn': {'id': 'second'}}))
    assert adapter.event(completed('second'))

    # Input during admission waits for the turn ID instead of racing a second start.
    adapter, sent = setup()
    first_admission = sent[-1]
    adapter.steer('[supervisor] steer during admission')
    assert sent[-1] is first_admission, 'a second admission raced the first'
    assert not adapter.event(reply(first_admission, {'turn': {'id': 'first'}}))
    assert sent[-1]['method'] == 'turn/steer' and sent[-1]['params']['expectedTurnId'] == 'first'

    # A steer rejected at the completion boundary is kept for the next turn,
    # never retried against the ending turn.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    adapter.steer('[supervisor] steer across the completion boundary')
    steer = sent[-1]
    with contextlib.redirect_stderr(io.StringIO()):
        assert not adapter.event({'id': steer['id'], 'error': {'code': -32600, 'message': 'turn mismatch'}})
    assert sent[-1] is steer, 'the rejected input was re-steered into the ending turn'
    assert not adapter.event(completed('first'))
    admission = sent[-1]
    assert admission['method'] == 'turn/start'
    assert admission['params']['input'][0]['text'].startswith('[supervisor] steer across')
    assert not adapter.event(reply(admission, {'turn': {'id': 'second'}}))
    assert adapter.event(completed('second'))

    # Long-running work holds an active turn open. A terminal turn ends the
    # stage while still reporting the calls that never completed.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    assert not adapter.event({'method': 'item/started', 'params': {'item': {
        'id': 'check', 'type': 'commandExecution', 'status': 'inProgress', 'command': 'python3 check.py'}}})
    assert not adapter.event({'method': 'item/commandExecution/outputDelta', 'params': {'itemId': 'check'}})
    assert adapter.event(completed('first'))
    assert adapter.busy == {'check': {'tool': 'commandExecution', 'command': 'python3 check.py'}}
    assert adapter.event({'method': 'item/completed', 'params': {'item': {
        'id': 'check', 'type': 'commandExecution', 'status': 'completed', 'exitCode': 0}}})
    assert adapter.busy == {}
    assert adapter.event({'id': 'refresh', 'method': 'account/chatgptAuthTokens/refresh', 'params': {}}), \
        'a host request after the turn ended reopened native work'
    adapter.steer('[supervisor] stop the abandoned check')
    assert not adapter.ended(), 'steering after an unfinished turn did not reopen native work'

    # Interrupt stops the running turn; its abandoned calls are not reported as
    # unfinished, and the input starts the next turn only after the stop.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    assert not adapter.event({'method': 'item/started', 'params': {'item': {
        'id': 'slow', 'type': 'commandExecution', 'status': 'inProgress', 'command': 'pytest -x'}}})
    adapter.interrupt('[supervisor] stop and fix the failing test')
    stop = sent[-1]
    assert stop['method'] == 'turn/interrupt' and stop['params']['turnId'] == 'first'
    assert not adapter.event(reply(stop, {}))
    assert sent[-1] is stop, 'input was steered into the turn being interrupted'
    assert not adapter.event({'method': 'turn/completed', 'params': {'turn': {'id': 'first', 'status': 'interrupted'}}})
    admission = sent[-1]
    assert admission['method'] == 'turn/start' and admission['params']['input'][0]['text'].startswith('[supervisor] stop')
    assert adapter.busy == {}
    assert not adapter.event(reply(admission, {'turn': {'id': 'second'}}))
    assert adapter.event(completed('second'))

    # A turn that completes before the interrupt arrives keeps the input for the next turn.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    adapter.interrupt('[supervisor] late interrupt')
    stop = sent[-1]
    assert not adapter.event(completed('first'))
    with contextlib.redirect_stderr(io.StringIO()):
        assert not adapter.event({'id': stop['id'], 'error': {'code': -32600, 'message': 'no active turn'}})
    assert sent[-1]['method'] == 'turn/start'

    # An interrupted turn that Cerebro did not request is still a failure.
    adapter, sent = setup()
    adapter.event(reply(sent[-1], {'turn': {'id': 'first'}}))
    try:
        adapter.event({'method': 'turn/completed', 'params': {'turn': {'id': 'first', 'status': 'interrupted'}}})
    except RuntimeError as error:
        assert 'interrupted' in str(error)
    else:
        raise AssertionError('an unrequested interruption was accepted as completion')

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

    # Claude replays input when it takes it into the conversation (verified
    # against Claude Code 2.1.289). Input taken mid-turn joins that turn and
    # shares its single result; input taken after a result starts another turn.
    def taken(text):
        frame = next(item for item in reversed(sent) if item.get('type') == 'user' and item['message']['content'] == text)
        return {**frame, 'isReplay': True}

    def result(text):
        return {'type': 'result', 'subtype': 'success', 'result': text}

    sent = []
    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    assert not adapter.event(taken('initial task'))
    adapter.steer('[supervisor] enforce the approved contract')
    assert not adapter.event(taken('[supervisor] enforce the approved contract'))
    assert adapter.event(result('steered result')), 'a mid-turn steer waited for a second result'

    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    assert not adapter.event(taken('initial task'))
    adapter.steer('[supervisor] sent as the turn finishes')
    assert not adapter.event(result('first result')), 'a result finished the stage before pending input was taken'
    assert not adapter.event(taken('[supervisor] sent as the turn finishes'))
    assert adapter.event(result('steered result'))

    # Inputs queued at a turn start can merge; Claude still replays each uuid.
    # A replay of input Cerebro did not send is not counted.
    sent = []
    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    adapter.steer('[supervisor] queued before the first turn')
    assert len({item['uuid'] for item in sent}) == 2
    assert not adapter.event({'type': 'user', 'isReplay': True, 'uuid': 'foreign', 'message': {'content': 'x'}})
    assert not adapter.event(taken('[supervisor] queued before the first turn'))
    assert not adapter.event(taken('initial task'))
    assert adapter.event(result('merged result'))

    # A turn Claude starts by itself is open until its result.
    assert not adapter.event({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'more'}]}})
    assert adapter.event(result('background follow-up'))

    # Interrupt (verified against Claude Code 2.1.289): the running turn ends with
    # an error result, then the new input runs as its own turn.
    sent = []
    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    assert not adapter.event(taken('initial task'))
    adapter.interrupt('[supervisor] stop and fix the failing test')
    assert sent[-2]['type'] == 'control_request' and sent[-2]['request']['subtype'] == 'interrupt'
    assert sent[-1]['message']['content'].startswith('[supervisor] stop')
    assert not adapter.event({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True})
    assert not adapter.event(taken('[supervisor] stop and fix the failing test'))
    assert adapter.event(result('fixed'))
    adapter.interrupt('[user] while idle')
    assert sent[-1]['type'] == 'user' and sent[-2]['type'] != 'control_request', 'an idle child was interrupted'

    adapter = Claude(sent.append, '')
    adapter.start('initial task')
    assert not adapter.event(taken('initial task'))
    assert not adapter.event({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'other', 'content': 'ok'}]}})
    assert not adapter.event({'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': 'check', 'name': 'Bash', 'input': {'command': 'python3 check.py'}}]}})
    assert adapter.event(result('final report'))
    assert adapter.busy == {'check': {'tool': 'Bash', 'command': 'python3 check.py'}}

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
    assert not adapter.ended(), 'low-level agent_end abandoned automatic native work'
    assert adapter.event({'type': 'agent_settled'})

    adapter, sent, emitted = pi_setup()
    assert not adapter.event(pi_reply(sent[-1]))
    assert not adapter.event({'type': 'agent_start'})
    assert not adapter.event({'type': 'tool_execution_start', 'toolCallId': 'check', 'toolName': 'bash',
                              'args': {'command': 'python3 check.py'}})
    assert adapter.event({'type': 'agent_settled'})
    assert adapter.busy == {'check': {'tool': 'bash', 'command': 'python3 check.py'}}

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
    assert not adapter.ended()
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

# Only the final result decides a Claude stage: an interrupted turn's error
# result is followed by the turn that replaces it.
import subprocess
PARSER = Path(__file__).resolve().parent.parent / 'lib' / 'python' / 'parse_stream.py'
with tempfile.TemporaryDirectory(prefix='cerebro-parse-tests-') as temporary:
    for results, code in (((('error_during_execution', None), ('success', 'fixed')), 0),
                          ((('success', 'first'), ('error_during_execution', None)), 4)):
        reply_path = Path(temporary) / 'reply'
        reply_path.unlink(missing_ok=True)
        stream = [{'type': 'system', 'subtype': 'init', 'session_id': 's'}]
        stream += [{'type': 'result', 'subtype': subtype, 'result': text} for subtype, text in results]
        parsed = subprocess.run([sys.executable, str(PARSER), str(reply_path), '', '', '', 'claude'],
                                input=''.join(json.dumps(item) + '\n' for item in stream),
                                text=True, capture_output=True)
        assert parsed.returncode == code, (results, parsed.returncode, parsed.stderr)
        if code == 0:
            assert reply_path.read_text() == 'fixed'

print('all checks passed')
