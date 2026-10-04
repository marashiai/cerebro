"""Rebuild what Jev would have seen just before each labelled moment in real Codex sessions.

Input: a private follow-up extract and labels (research output). Output: private
cases (JSONL) in a gitignored directory. Each case holds the user's request and
later messages, the agent turn the user reacted to as normalized events,
earlier history, and the repository's instruction files. The user's actual next
message is kept as `answer_key` and never sent to Jev.
"""

import argparse
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import re
import subprocess

# Research categories -> Jev reasons. Writing style and product taste are not
# judged from the event stream and are excluded.
REASONS = {
    'OVERCLAIM': 'unverified_claim', 'WRONGFACT': 'unverified_claim', 'DIAG': 'unverified_claim',
    'SCOPE': 'scope_creep', 'REINVENT': 'reinvention', 'MISREAD': 'misread_request',
    'CEREMONY': 'ceremony', 'PUNT': 'ceremony', 'COMPAT': 'standing_rule', 'GIT': 'standing_rule',
    'TOOLPREF': 'standing_rule', 'UNDERSCOPE': 'partial_fix', 'ACTFIRST': 'act_before_answer',
}

SECRETS = [
    re.compile(r'(?i)\b(sk|pk|rk)-[a-z0-9_\-]{16,}'), re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'), re.compile(r'(?i)bearer\s+[a-z0-9._\-]{16,}'),
    re.compile(r'(?i)((?:api[_-]?key|secret|token|password|passwd)["\']?\s*[:=]\s*["\']?)[^\s"\',]{8,}'),
    re.compile(r'\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}'),
]


def scrub(text):
    for pattern in SECRETS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else '') + '[REDACTED]', text)
    return text


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict))
    return ''


def output_of(raw):
    """Codex code-mode outputs embed JSON results with exit codes; plain outputs are text."""
    text = text_of(raw) if not isinstance(raw, str) else raw
    exit_code = None
    found = re.search(r'"exit_code"\s*:\s*(-?\d+)', text) or re.search(r'(?i)exit(?:ed with)? code:?\s*(-?\d+)', text)
    if found:
        exit_code = int(found.group(1))
    return text, exit_code


def patch_files(text):
    return re.findall(r'\*\*\* (?:Update|Add|Delete) File: (\S+)', text)


def session_items(path):
    """Ordered user messages and agent activity from one Codex rollout."""
    items, calls = [], {}
    with open(path, errors='replace') as source:
        meta = json.loads(source.readline()).get('payload', {})
        for line in source:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('type') != 'response_item':
                continue
            stamp = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).timestamp()
            payload = record.get('payload') or {}
            kind = payload.get('type')
            if kind == 'message' and payload.get('role') == 'user':
                text = text_of(payload.get('content')).strip()
                if text and not text.startswith(('<', '# AGENTS.md')):
                    items.append({'role': 'user', 'text': text, 'time': stamp})
            elif kind == 'message' and payload.get('role') == 'assistant':
                text = text_of(payload.get('content')).strip()
                if text:
                    items.append({'role': 'agent', 'time': stamp, 'event': {'kind': 'message', 'text': text}})
            elif kind in ('function_call', 'custom_tool_call'):
                body = payload.get('input') if kind == 'custom_tool_call' else payload.get('arguments')
                body = str(body or '')
                name = payload.get('name') or ''
                if 'Begin Patch' in body:
                    event = {'kind': 'file_change', 'files': patch_files(body), 'output': body[-4000:]}
                elif name in ('exec', 'exec_command', 'shell', 'local_shell') or 'exec_command' in body:
                    event = {'kind': 'command', 'command': body[:1500]}
                else:
                    event = {'kind': 'tool', 'text': (name + ' ' + body)[:300]}
                items.append({'role': 'agent', 'time': stamp, 'event': event})
                calls[payload.get('call_id')] = event
            elif kind in ('function_call_output', 'custom_tool_call_output'):
                event = calls.get(payload.get('call_id'))
                if event and event['kind'] == 'command':
                    text, exit_code = output_of(payload.get('output'))
                    event['output'] = text[-2000:]
                    if exit_code is not None:
                        event['exit'] = exit_code
    return meta, items


def scrub_event(event):
    return {key: scrub(value) if isinstance(value, str) else value for key, value in event.items()}


def repository(cwd):
    """The same git facts `cerebro run` gathers, read from the repository as it is now."""
    def git(*args):
        result = subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True)
        return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else None

    def limit(items, count):
        return items[:count] + [f'... {len(items) - count} more'] if len(items) > count else items

    branch = git('rev-parse', '--abbrev-ref', 'HEAD') if cwd and Path(cwd).is_dir() else None
    if not branch:
        return {'git': False, 'cwd': cwd}
    return {'branch': branch[0], 'branches': limit(git('branch', '--format=%(refname:short)') or [], 20),
            'git_identity': ' '.join((git('config', 'user.name') or []) + (git('config', 'user.email') or [])),
            'recent_authors': limit(git('log', '-5', '--format=%an <%ae>') or [], 5),
            'files': limit(git('ls-files') or [], 400)}


def build(record, path, label, category):
    meta, items = session_items(path)
    users = [index for index, item in enumerate(items) if item['role'] == 'user']
    target = next((index for index in users if items[index]['text'][:1500] == record['user'][:1500]), None)
    if target is None or not users or target == users[0]:
        return None
    turn_start = max(index for index in users if index < target)
    origin = items[users[0]]['time']
    for item in items:
        if item['role'] == 'agent':
            item['event']['at'] = round(item['time'] - origin, 1)
    turn = [item['event'] for item in items[turn_start + 1:target] if item['role'] == 'agent']
    if not turn:
        return None
    earlier = [item['event'] for item in items[:turn_start] if item['role'] == 'agent']
    messages = [items[index]['text'] for index in users if index < target]
    cwd = meta.get('cwd') or ''
    rules = []
    for name in ('AGENTS.md', 'CLAUDE.md'):
        candidate = Path(cwd) / name
        if candidate.is_file():
            rules.append(name + ':\n' + scrub(candidate.read_text(errors='replace')))
    return {
        'label': label, 'category': category, 'expected_reason': REASONS.get(category),
        'session': os.path.basename(path), 'timestamp': meta.get('timestamp'),
        'request': scrub(messages[0]), 'later_messages': [scrub(text) for text in messages[1:]],
        'history': [scrub_event(event) for event in earlier[-30:]],
        'events': [scrub_event(event) for event in turn[-16:]],
        'rules': rules, 'repository': repository(cwd),
        'answer_key': scrub(record['user']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('followups', type=Path)
    parser.add_argument('labels', type=Path, help='JSON: {"corrections": {index: category}, "fine": [index, ...]}')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--holdout', type=float, default=0.4, help='share of sessions, by date, held back')
    args = parser.parse_args()
    records = json.loads(args.followups.read_text())
    labels = json.loads(args.labels.read_text())
    sessions = {os.path.basename(path)[:40]: path
                for path in glob.glob(os.path.expanduser('~/.codex/sessions/**/*.jsonl'), recursive=True)}
    cases = []
    wanted = [(int(index), 'concern', category) for index, category in labels['corrections'].items()
              if category in REASONS] + [(index, 'clear', None) for index in labels['fine']]
    for index, label, category in wanted:
        record = records[index]
        case = build(record, sessions[record['session']], label, category)
        if case:
            cases.append({'id': f'c{index}', **case})
    order = sorted({case['session'] for case in cases}, key=lambda name: name)
    cut = int(len(order) * (1 - args.holdout))
    held = set(order[cut:])
    for case in cases:
        case['split'] = 'holdout' if case['session'] in held else 'dev'
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w') as stream:
        for case in cases:
            stream.write(json.dumps(case) + '\n')
    counts = {}
    for case in cases:
        key = (case['split'], case['label'])
        counts[key] = counts.get(key, 0) + 1
    print(len(cases), 'cases', counts)


if __name__ == '__main__':
    main()
