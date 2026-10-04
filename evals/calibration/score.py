"""Score jev-replay output against the cases' known answers, across thresholds."""

import argparse
import collections
import json
from pathlib import Path


def decision(result, threshold):
    """The check that would nudge at a threshold, or None."""
    classification = (result.get('record') or {}).get('classification')
    if not classification or result.get('error'):
        return None
    best = classification['best']
    if classification['scores'][best] >= threshold and classification['evidence'] != 'none':
        return best
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', type=Path)
    parser.add_argument('results', type=Path)
    parser.add_argument('--split', default='dev')
    args = parser.parse_args()
    cases = {case['id']: case for case in map(json.loads, args.cases.read_text().splitlines())}
    results = [json.loads(line) for line in args.results.read_text().splitlines()]
    results = [result for result in results if cases[result['id']]['split'] == args.split]
    errors = sum(bool(result.get('error')) for result in results)
    print(f'split={args.split} cases={len(results)} jev_errors={errors}')
    print('threshold  caught/positives  right_check  false_alarms/negatives')
    for threshold in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        caught = right = false = positives = negatives = 0
        by_reason = collections.Counter()
        totals = collections.Counter()
        for result in results:
            case = cases[result['id']]
            nudged = decision(result, threshold)
            if case['label'] == 'concern':
                positives += 1
                totals[case['expected_reason']] += 1
                if nudged:
                    caught += 1
                    by_reason[case['expected_reason']] += 1
                    right += nudged == case['expected_reason']
            else:
                negatives += 1
                false += bool(nudged)
        print(f'{threshold:9.1f}  {caught:3d}/{positives:<3d} ({caught / max(positives, 1):4.0%})  {right:3d}'
              f'          {false:3d}/{negatives:<3d} ({false / max(negatives, 1):4.0%})')
        if threshold in (0.3, 0.5):
            print('           by reason:', {reason: f'{by_reason[reason]}/{totals[reason]}' for reason in totals})


if __name__ == '__main__':
    main()
