"""Claude native prompt hooks record input and bind the chosen session UUID."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent


with tempfile.TemporaryDirectory(prefix='cerebro-claude-hook-tests-') as temporary:
    home = Path(temporary)
    sid = '00000000-0000-4000-8000-000000000001'
    session = home / 'sessions' / sid
    session.mkdir(parents=True)
    (session / 'transcript.jsonl').touch()
    metadata = session / 'metadata.json'
    metadata.write_text(json.dumps({'id': sid, 'backend': 'claude',
                                    'foreign_session_id': 'previous-id', 'last_touched': 'previous-time'}))
    prompt = 'Keep the approved scope.\nLiteral $(touch unexpected) and `code`.'
    payload = {'session_id': sid, 'transcript_path': str(home / 'native-session.jsonl'),
               'cwd': str(home), 'permission_mode': 'dontAsk',
               'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}
    environment = {'HOME': os.environ['HOME'], 'PATH': os.environ['PATH'], 'CEREBRO_HOME': str(home)}
    result = subprocess.run(['bash', str(root / 'lib' / 'payloads' / 'hook.sh')],
                            input=json.dumps(payload), text=True, capture_output=True,
                            env=environment, cwd=home, timeout=5)
    assert result.returncode == 0, result.stderr
    transcript = [json.loads(line) for line in (session / 'transcript.jsonl').read_text().splitlines()]
    assert len(transcript) == 1 and transcript[0]['kind'] == 'user'
    assert transcript[0]['text'] == prompt
    updated = json.loads(metadata.read_text())
    assert updated['foreign_session_id'] == sid and updated['backend'] == 'claude'
    assert updated['last_touched'] == transcript[0]['ts'] and updated['last_touched'] != 'previous-time'
    assert (home / 'current-session').resolve() == session.resolve()
    assert not (home / 'unexpected').exists(), 'hook evaluated native prompt content'

print('all checks passed')
