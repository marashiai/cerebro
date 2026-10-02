"""Search complete paths, including spaces, newlines and glob characters."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='cerebro recall spaces ') as directory:
    home = Path(directory)
    for name in ('ordinary', 'session with spaces', 'session\nwith-newline', 'session[glob]'):
        session = home / 'sessions' / name
        session.mkdir(parents=True)
        (session / 'transcript.jsonl').write_text(json.dumps({'text': 'RECALL_SENTINEL alpha'}) + '\n')
    session = home / 'sessions' / 'ordinary'
    env = {**os.environ, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session.name,
           'CEREBRO_SESSION_DIR': str(session), 'CEREBRO_JEV_ENABLED': '0'}
    for query, expected in [('RECALL_SENTINEL', 4), ('missing alpha', 4), ('no_matching_term', 0)]:
        result = subprocess.run([str(ROOT / 'bin/cerebro'), 'recall', query], env=env,
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0 and result.stdout.count('RECALL_SENTINEL') == expected, result
print('all checks passed')
