"""Assess a completed review against bounded task and repository evidence."""

import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from jev import ENDPOINT, Jev, number, write_trace


def clipped(text, limit):
    return {'text': text[:limit], 'truncated': len(text) > limit,
            'sha256': hashlib.sha256(text.encode()).hexdigest()}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], timeout=20).decode('utf-8', errors='replace')


def write_private(path, content):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def context(repo, base, report, criteria, session):
    repo = repo.resolve()
    review = report.read_text()
    diff = clipped(git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--unified=12', base, '--'), 24000)
    tracked = set(git(repo, 'ls-files', '-z').split('\0')) - {''}
    untracked = set(git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')) - {''}
    anchors = []
    for name in tracked | untracked:
        for match in re.finditer(re.escape(name) + r':([1-9][0-9]*)', review):
            anchors.append((match.start(), name, int(match.group(1))))
    requested = list(dict.fromkeys((name, line) for _, name, line in sorted(anchors)))
    requested += [(name, 1) for name in sorted(untracked) if not any(item[0] == name for item in requested)]
    evidence = []
    lines = diff['text'].splitlines()
    for offset in range(0, len(lines), 60):
        evidence.append({'id': 'diff-' + str(offset + 1), 'kind': 'diff',
                         'text': '\n'.join(lines[offset:offset + 60])})
    if not lines:
        evidence.append({'id': 'diff-1', 'kind': 'diff', 'text': 'No tracked changes against the review base.'})
    budget, omitted = 24000, []
    for name, line in requested:
        path = repo / name
        if budget <= 0 or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(repo):
            omitted.append({'path': name, 'line': line})
            continue
        start = max(1, line - 20)
        with path.open(encoding='utf-8', errors='replace') as source:
            excerpt = ''.join(f'{number}: {text}' for number, text in
                              enumerate(itertools.islice(source, start - 1, line + 20), start))
            remaining = bool(source.readline())
        excerpt = clipped(excerpt, min(budget, 4000))
        excerpt['truncated'] |= start > 1 or remaining
        budget -= len(excerpt['text'])
        evidence.append({'id': 'source-' + str(len(evidence) + 1), 'kind': 'source',
                         'path': name, 'line': line, 'start_line': start,
                         'end_line': start + len(excerpt['text'].splitlines()) - 1, **excerpt})
    spec = session / 'spec.md'
    return {'repo': str(repo), 'base': base, 'head': git(repo, 'rev-parse', 'HEAD').strip(),
            'requirements': clipped(spec.read_text() if spec.exists() else '', 8000),
            'criteria': clipped(Path(criteria).read_text() if criteria else '', 8000),
            'review': clipped(review, 24000), 'diff': {key: value for key, value in diff.items() if key != 'text'},
            'evidence': evidence, 'omitted_sources': omitted}


def questions_for(state):
    questions = json.loads((Path(__file__).resolve().parent.parent /
                            'payloads' / 'jev' / 'review-questions.json').read_text())
    questions['evidence'] = {
        'type': 'choice',
        'instructions': 'Select the supplied code excerpt that most strongly supports your assessment. '
                        'Select none if no supplied code excerpt supports it. Never invent evidence.',
        'criteria': {'none': 'No supplied code excerpt establishes the assessment',
                     **{item['id']: 'Code excerpt ' + item['id'] for item in state['evidence']}},
    }
    return questions


def assess(repo, base, report, criteria, session):
    client = Jev(os.environ.get('CEREBRO_JEV_API_KEY', ''),
                 os.environ.get('CEREBRO_JEV_MODEL', 'jev-latest'),
                 os.environ.get('CEREBRO_JEV_ENDPOINT', ENDPOINT))
    confidence = float(os.environ.get('CEREBRO_JEV_CONFIDENCE', '0.8'))
    if not number(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Jev confidence must be between 0 and 1')
    state = context(repo, base, report, criteria, session)
    original = report.read_text()
    if hashlib.sha256(original.encode()).hexdigest() != state['review']['sha256']:
        raise ValueError('review changed while collecting Jev evidence; rerun the review')
    trace = report.with_suffix('.jev.jsonl')
    questions = questions_for(state)
    response = client.evaluate(state, questions, trace)
    if context(repo, base, report, criteria, session) != state:
        write_trace(trace, {'type': 'assessment_error', 'request_id': response['request_id'],
                            'error': 'review inputs changed during assessment'})
        raise ValueError('review inputs changed during Jev assessment; rerun the review')
    answers = response['answers']
    evidence = next((item for item in state['evidence'] if item['id'] == answers['evidence']['choice']), None)
    validity, usefulness = (answers[name]['choice'] if answers[name]['confidence'] >= confidence else 'uncertain'
                            for name in ('validity', 'usefulness'))
    # Choosing between overlapping excerpts is distinct from confidence in the verdict.
    if evidence is None:
        validity = 'uncertain'
    if any(state[name]['truncated'] for name in ('requirements', 'criteria', 'review')) and validity == 'supported':
        validity = 'uncertain'
    assessment = {'request_id': response['request_id'], 'model': response['model'],
                  'confidence_threshold': confidence,
                  'validity': validity, 'usefulness': usefulness, 'answers': answers,
                  'evidence': evidence, 'base': base, 'head': state['head'],
                  'context_sha256': hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
                  'context_truncated': any(state[name]['truncated'] for name in ('requirements', 'criteria', 'review', 'diff'))
                      or bool(state['omitted_sources']) or any(item.get('truncated') for item in state['evidence'])}
    assessment_path = report.with_suffix('.assessment.json')
    write_private(assessment_path, json.dumps(assessment, indent=2) + '\n')
    reason = questions['reason']['criteria'][answers['reason']['choice']]
    header = ('## Jev review assessment\n\n'
              f'- Validity: **{validity}** (Jev answer: {answers["validity"]["choice"]}, confidence {answers["validity"]["confidence"]:.2f}).\n'
              f'- Usefulness: **{usefulness}** (Jev answer: {answers["usefulness"]["choice"]}, confidence {answers["usefulness"]["confidence"]:.2f}).\n'
              f'- Basis: {reason}\n'
              f'- Code evidence: {answers["evidence"]["choice"]}; context truncated: {str(assessment["context_truncated"]).lower()}.\n'
              f'- [Assessment and cited excerpt]({assessment_path}) · [Jev request/response trace]({trace})\n\n'
              'Assess these advisory labels alongside the original findings. They grant no delivery authority '
              'and do not replace required verification.\n\n---\n\n')
    write_private(report, header + original)


if __name__ == '__main__':
    try:
        repo, base, report, criteria = sys.argv[1:]
        assess(Path(repo).resolve(), base, Path(report), criteria, Path(os.environ['CEREBRO_SESSION_DIR']))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit('cerebro: Jev review assessment failed: ' + str(error))
