"""Paired evaluation: the same native agent with and without Jev, on frozen tasks.

Decision rule (fixed before any run; see README): Jev is "better" only when, over
all (task, repetition) pairs, the Jev arm wins more discordant pairs than it
loses with a one-sided sign test p <= 0.10, and its mean wall time is at most
20% above the bare arm's. Otherwise the result is "not better".
"""

import argparse
import json
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import inventory_fixture  # noqa: E402
import job_fixture  # noqa: E402
import lease_fixture  # noqa: E402
import patch_fixture  # noqa: E402
from fixtures import file_hashes, write_json  # noqa: E402

CASES = {'inventory': inventory_fixture, 'patch': patch_fixture, 'lease': lease_fixture, 'job': job_fixture}
ARMS = ('bare', 'jev')
MAX_P, MAX_OVERHEAD = 0.10, 0.20


def usage(log_path, model, prices):
    """Return agent and Jev token totals and an estimated price, or None where unknown."""
    agent, jev_in, jev_out, nudges, calls, failures = None, 0, 0, [], 0, 0
    records = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
    for index, record in enumerate(records):
        line = record.get('line')
        if record['type'] == 'native' and isinstance(line, dict) and line.get('method') == 'thread/tokenUsage/updated':
            agent = line['params']['tokenUsage']['total']
        elif record['type'] == 'jev':
            calls += 1
            failures += 'error' in record
            if isinstance(record.get('raw'), dict):
                jev_in += record['raw'].get('usage', {}).get('input_tokens', 0)
                jev_out += record['raw'].get('usage', {}).get('output_tokens', 0)
        elif record['type'] == 'nudge':
            reply = next((later['event']['text'] for later in records[index + 1:]
                          if later['type'] == 'event' and later['event']['kind'] == 'message'), None)
            nudges.append({**record['nudge'], 'next_agent_message': reply})
    agent_cost = jev_cost = None
    if agent and model in prices:
        rate = prices[model]
        uncached = agent['inputTokens'] - agent['cachedInputTokens']
        agent_cost = (uncached * rate['input_per_million'] + agent['cachedInputTokens'] * rate['cached_input_per_million']
                      + agent['outputTokens'] * rate['output_per_million']) / 1e6
    jev_model = os.environ.get('JEV_MODEL', 'jev-latest')
    if not calls:
        jev_cost = 0.0
    elif jev_model in prices:
        jev_cost = jev_in * prices[jev_model]['input_per_million'] / 1e6
    cost = None if agent_cost is None or jev_cost is None else agent_cost + jev_cost
    return {'agent_tokens': agent, 'jev_calls': calls, 'jev_failures': failures, 'jev_input_tokens': jev_in,
            'jev_output_tokens': jev_out, 'estimated_usd': cost, 'nudges': nudges}


def trial(args, binary, case, arm, repeat, prices):
    directory = args.out / f'r{repeat:02d}' / case / arm
    repo = directory / 'repo'
    CASES[case].seed(repo)
    before = file_hashes(repo)
    log_path = directory / 'run.jsonl'
    command = [str(binary), 'run', '--backend', args.backend, '--dir', str(repo), '--log', str(log_path),
               '--timeout', f'{args.timeout}s']
    for flag, value in (('--model', args.model), ('--effort', args.effort)):
        if value:
            command += [flag, value]
    if arm == 'bare':
        command.append('--no-jev')
    else:
        command += ['--threshold', str(args.threshold), '--max-nudges', str(args.max_nudges)]
    command.append(CASES[case].REQUIREMENTS)
    env = dict(os.environ)
    if arm == 'bare':
        env.pop('JEV_API_KEY', None)
    started = time.monotonic()
    with (directory / 'stdout.txt').open('w') as out, (directory / 'stderr.txt').open('w') as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            exit_code = process.wait(timeout=args.timeout + 60)
        except subprocess.TimeoutExpired:
            # SIGTERM lets cerebro end the agent's own process group first.
            os.killpg(process.pid, signal.SIGTERM)
            try:
                exit_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                exit_code = process.wait()
    elapsed = time.monotonic() - started
    graded = CASES[case].grade(repo, before)
    record = {'case': case, 'arm': arm, 'repeat': repeat, 'exit_code': exit_code, 'elapsed_seconds': round(elapsed, 2),
              'delivered': exit_code == 0 and graded['correct'], 'correct_code': graded['correct'],
              'failed_checks': sorted(name for name, passed in graded['checks'].items() if not passed),
              'scope_pass': graded['scope_pass'], 'changed_files': graded['changed_files'],
              **usage(log_path, args.model, prices)}
    write_json(directory / 'result.json', record)
    return record


def sign_test(wins, losses):
    """One-sided exact sign test: probability of at least `wins` successes in wins+losses fair trials."""
    total = wins + losses
    if total == 0:
        return 1.0
    return sum(math.comb(total, k) for k in range(wins, total + 1)) / 2 ** total


def summarize(records):
    arms = {}
    for arm in ARMS:
        rows = [row for row in records if row['arm'] == arm]
        if not rows:
            continue
        costs = [row['estimated_usd'] for row in rows if row['estimated_usd'] is not None]
        arms[arm] = {'trials': len(rows), 'delivered': sum(row['delivered'] for row in rows),
                     'mean_seconds': round(sum(row['elapsed_seconds'] for row in rows) / len(rows), 1),
                     'mean_usd': round(sum(costs) / len(costs), 4) if len(costs) == len(rows) else None,
                     'nudges': sum(len(row['nudges']) for row in rows),
                     'jev_calls': sum(row['jev_calls'] for row in rows),
                     'jev_failures': sum(row['jev_failures'] for row in rows)}
    pairs = {}
    for row in records:
        pairs.setdefault((row['case'], row['repeat']), {})[row['arm']] = row['delivered']
    complete = [pair for pair in pairs.values() if set(pair) == set(ARMS)]
    wins = sum(pair['jev'] and not pair['bare'] for pair in complete)
    losses = sum(pair['bare'] and not pair['jev'] for pair in complete)
    p = sign_test(wins, losses)
    overhead = (arms['jev']['mean_seconds'] / arms['bare']['mean_seconds'] - 1
                if {'jev', 'bare'} <= arms.keys() and arms['bare']['mean_seconds'] else None)
    better = wins > losses and p <= MAX_P and overhead is not None and overhead <= MAX_OVERHEAD
    return {'arms': arms, 'pairs': len(complete), 'jev_wins': wins, 'jev_losses': losses,
            'sign_test_p': round(p, 4), 'time_overhead': None if overhead is None else round(overhead, 3),
            'decision': 'better' if better else 'not better'}


def report(summary, records, args):
    lines = [f'# Bare agent vs agent + Jev', '',
             f'Backend `{args.backend}`, model `{args.model or "default"}`, effort `{args.effort or "default"}`, '
             f'cases {", ".join(args.cases)}, {args.repeat} repetitions, {args.timeout}s deadline.', '',
             '| Arm | Delivered | Mean seconds | Mean est. USD | Nudges | Jev calls (failed) |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for arm, row in summary['arms'].items():
        usd = 'unknown' if row['mean_usd'] is None else f'{row["mean_usd"]:.4f}'
        lines.append(f'| {arm} | {row["delivered"]}/{row["trials"]} | {row["mean_seconds"]} | {usd} | {row["nudges"]} | '
                     f'{row["jev_calls"]} ({row["jev_failures"]}) |')
    lines += ['', f'Paired: {summary["pairs"]} pairs; Jev won {summary["jev_wins"]}, lost {summary["jev_losses"]} '
              f'(one-sided sign test p = {summary["sign_test_p"]}); time overhead {summary["time_overhead"]}.',
              f'**Decision: {summary["decision"]}** (better requires more wins than losses, p <= {MAX_P}, '
              f'and at most {int(MAX_OVERHEAD * 100)}% extra time).', '', '## Trials', '',
              '| Case | Rep | Arm | Delivered | Seconds | Failed checks | Nudges |', '| --- | ---: | --- | --- | ---: | --- | ---: |']
    for row in sorted(records, key=lambda item: (item['case'], item['repeat'], item['arm'])):
        lines.append(f'| {row["case"]} | {row["repeat"]} | {row["arm"]} | {row["delivered"]} | {row["elapsed_seconds"]} | '
                     f'{", ".join(row["failed_checks"]) or "-"} | {len(row["nudges"])} |')
    nudged = [(row, nudge) for row in records for nudge in row['nudges']]
    if nudged:
        lines += ['', '## Nudges and the agent\'s next message', '']
        for row, nudge in nudged:
            reply = (nudge['next_agent_message'] or '(none)').replace('\n', ' ')[:300]
            lines.append(f'- {row["case"]} r{row["repeat"]} `{nudge["reason"]}`'
                         f'{" (interrupt)" if nudge["interrupt"] else ""}: {reply}')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cases', nargs='+', choices=sorted(CASES), default=['inventory', 'patch'])
    parser.add_argument('--arms', nargs='+', choices=ARMS, default=list(ARMS))
    parser.add_argument('--repeat', type=int, default=10)
    parser.add_argument('--backend', default='codex', choices=['codex', 'claude', 'pi'])
    parser.add_argument('--model', default='')
    parser.add_argument('--effort', default='')
    parser.add_argument('--timeout', type=int, default=600, help='seconds per run, the same for both arms')
    parser.add_argument('--seed', type=int, default=42, help='randomizes arm order within each pair')
    parser.add_argument('--threshold', type=float, default=0.8, help='Jev confidence needed for a nudge')
    parser.add_argument('--max-nudges', type=int, default=3)
    parser.add_argument('--prices', type=Path, default=HERE / 'prices-2026-10-03.json')
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    args = parser.parse_args()
    args.out = args.out.resolve()  # runs execute inside their seeded repositories
    if args.out.exists():
        parser.error('output directory already exists: ' + str(args.out))
    if 'jev' in args.arms and not os.environ.get('JEV_API_KEY'):
        parser.error('the jev arm needs JEV_API_KEY')
    args.out.mkdir(parents=True)
    binary = args.out / 'bin' / 'cerebro'
    subprocess.run(['go', 'build', '-o', str(binary), './cmd/cerebro'], cwd=ROOT, check=True)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'cmd', 'internal', 'evals/*.py'], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip())
    write_json(args.out / 'manifest.json', {**{key: str(value) for key, value in vars(args).items()},
                                            'jev_model': os.environ.get('JEV_MODEL', 'jev-latest'),
                                            'commit': commit, 'dirty_source': dirty})
    prices = json.loads(args.prices.read_text())['models']
    order = random.Random(args.seed)
    records = []
    for repeat in range(1, args.repeat + 1):
        for case in args.cases:
            arms = list(args.arms)
            order.shuffle(arms)
            for arm in arms:
                record = trial(args, binary, case, arm, repeat, prices)
                records.append(record)
                print(f'{case} r{repeat} {arm}: delivered={record["delivered"]} {record["elapsed_seconds"]}s '
                      f'nudges={len(record["nudges"])} failed={record["failed_checks"]}', flush=True)
                with (args.out / 'results.jsonl').open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
    summary = summarize(records)
    write_json(args.out / 'summary.json', summary)
    (args.out / 'report.md').write_text(report(summary, records, args))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
