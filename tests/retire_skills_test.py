"""Upgrade removes only exact generated obsolete payloads, retaining custom skills."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib/python'))
from retire_skills import retire

GENERATED = '''---
name: cerebro-worker
description: Perform a delegated Cerebro task using the shared engineering skill and a terminal handoff.
---
You are a Cerebro child. Apply the engineering skill supplied with these
instructions. Read applicable repository instructions before acting. Work
only on the delegated task in the selected checkout, preserving unrelated
changes. The task packet defines delivery authority; do not infer permission
to commit, push, open PRs or deploy from the command name.

Return the result, exact worktree/branch/base/head when applicable, verification
evidence, remaining risks and any decision needed from the parent. Do not
claim completion when the task or required verification remains unfinished.'''

with tempfile.TemporaryDirectory(prefix='cerebro-upgrade-') as temporary:
    home = Path(temporary)
    canonical = home / '.agents/skills/cerebro-worker'
    linked = home / '.claude/skills/cerebro-worker'
    canonical.mkdir(parents=True)
    linked.parent.mkdir(parents=True)
    (canonical / 'SKILL.md').write_text(GENERATED)
    linked.symlink_to('../../.agents/skills/cerebro-worker')
    personal = home / '.agents/skills/personal'
    personal.mkdir()
    (personal / 'SKILL.md').write_text('User-owned instructions')
    customized = home / '.agents/skills/cerebro-supervisor'
    customized.mkdir()
    (customized / 'SKILL.md').write_text('User-customized Cerebro supervisor')
    retire(home)
    assert not canonical.exists() and not linked.is_symlink()
    assert (personal / 'SKILL.md').read_text() == 'User-owned instructions'
    assert (customized / 'SKILL.md').read_text() == 'User-customized Cerebro supervisor'
print('all checks passed')
