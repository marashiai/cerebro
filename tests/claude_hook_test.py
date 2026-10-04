"""Claude native prompt hooks record input and bind the chosen session UUID."""

import json
import os
from pathlib import Path
import shutil
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
                                    'foreign_session_id': sid, 'last_touched': 'previous-time'}))
    prompt = 'Keep the approved scope.\nLiteral $(touch unexpected) and `code`.\n\n'
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
    inputs = json.loads((session / 'user-inputs.json').read_text())
    assert len(inputs) == 1 and inputs[0]['content'] == [{'type': 'text', 'text': prompt}]
    updated = json.loads(metadata.read_text())
    assert updated['foreign_session_id'] == sid and updated['backend'] == 'claude'
    assert updated['last_touched'] == transcript[0]['ts'] and updated['last_touched'] != 'previous-time'
    assert (home / 'current-session').resolve() == session.resolve()
    assert not (home / 'unexpected').exists(), 'hook evaluated native prompt content'
    before = (session / 'user-inputs.json').read_bytes()
    result = subprocess.run(['bash', str(root / 'lib' / 'payloads' / 'hook.sh')],
                            input=json.dumps(payload), text=True, capture_output=True,
                            env={**environment, 'CEREBRO_INPUT_OWNER': 'external'}, timeout=5)
    assert result.returncode == 0 and (session / 'user-inputs.json').read_bytes() == before
    result = subprocess.run(['bash', str(root / 'lib' / 'payloads' / 'hook.sh')],
                            input=json.dumps({**payload, 'prompt': None}), text=True, capture_output=True,
                            env=environment, timeout=5)
    assert result.returncode == 2 and 'capture failed' in result.stderr
    assert (session / 'user-inputs.json').read_bytes() == before
    repository = home / 'repository with spaces'
    (repository / 'lib' / 'payloads').mkdir(parents=True)
    copied = repository / 'lib' / 'payloads' / 'hook.sh'
    shutil.copyfile(root / 'lib' / 'payloads' / 'hook.sh', copied)
    (repository / 'lib' / 'python').symlink_to(root / 'lib' / 'python', target_is_directory=True)
    result = subprocess.run(['bash', '-c', 'CEREBRO_LIB_DIR="$1"; . "$1/payloads.sh"; cerebro_settings_json "$2"',
                             '_', str(root / 'lib'), str(copied)],
                            text=True, capture_output=True, env=environment, timeout=5)
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
    result = subprocess.run(['bash', '-c', command], input=json.dumps(payload),
                            text=True, capture_output=True, env=environment, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads((session / 'user-inputs.json').read_text())[-1]['content'][0]['text'] == prompt

print('all checks passed')
