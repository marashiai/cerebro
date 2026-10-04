"""Remove only byte-identical Cerebro-generated obsolete role skill payloads."""
import hashlib
import json
from pathlib import Path
import sys


def retire(home):
    manifest = Path(__file__).resolve().parent.parent / 'payloads/retired-skills.json'
    for topic, digest in json.loads(manifest.read_text()).items():
        removed = False
        for root in ('.agents', '.claude'):
            directory = Path(home) / root / 'skills' / topic
            if directory.is_symlink():
                if root == '.claude' and removed and str(directory.readlink()) == '../../.agents/skills/' + topic:
                    directory.unlink()
                continue
            skill = directory / 'SKILL.md'
            if skill.is_file() and not skill.is_symlink() and hashlib.sha256(skill.read_bytes()).hexdigest() == digest:
                skill.unlink()
                removed = True
                if not any(directory.iterdir()):
                    directory.rmdir()


if __name__ == '__main__':
    retire(sys.argv[1])
