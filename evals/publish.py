#!/usr/bin/env python3
"""Publish a sanitized, immutable report from a private eval run."""

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import quote

from stats import BOOTSTRAP_MIN_CASES, ROLES, TOKEN_KEYS, number, summarize


START = '<!-- evals:overview:start -->'
END = '<!-- evals:overview:end -->'
LABELS = {'bare': 'Bare agent', 'cerebro': 'Cerebro', 'cerebro_jev': 'Cerebro + Jev',
          'without_jev': 'Without Jev', 'with_jev': 'With Jev', 'protocol': 'Protocol'}
COLORS = {'bare': '#748094', 'cerebro': '#6456d8', 'cerebro_jev': '#009b87',
          'without_jev': '#748094', 'with_jev': '#009b87', 'protocol': '#6456d8'}


def formatted(value, digits=2, signed=False):
    return 'unknown' if value is None else format(value, ('+' if signed else '') + ',.' + str(digits) + 'f')


def interval(value, scale=1, digits=1):
    return 'unavailable' if value is None else '–'.join(formatted(item * scale, digits) for item in value)


def markdown(text):
    return html.escape(text).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')


def overview(report, link):
    lines = ['[Latest published results](' + link + ')', '']
    cohorts = [(index, cohort) for index, cohort in enumerate(report['cohorts'], 1)
               if cohort['mode'] == 'comparison']
    if not cohorts:
        return '\n'.join(lines + ['No product comparison was measured in this run. Protocol and Jev ablation '
                                 'results are reported separately.'])
    for index, cohort in cohorts:
        if not cohort['matched_units']:
            lines += ['Configuration `%s`: no complete matched units; %d incomplete units. '
                      'Comparative outcome, time, and price estimates are unavailable.' % (
                          cohort['configuration'], len(cohort['incomplete_units'])), '']
            continue
        parts = ['%s %d/%d' % (LABELS[arm], stats['passed'], stats['trials'])
                 for arm, stats in cohort['arms'].items()]
        delta = cohort['deltas'][0]
        lines.append('Configuration `%s`: **%s** shared task outcomes; Cerebro − bare **%s percentage points**. '
                     '%d matched %s across %d distinct cases; %d incomplete units excluded. '
                     'Small selected CSV fixtures; descriptive results.' % (
                         cohort['configuration'], '; '.join(parts), formatted(delta['pass_rate_delta_pp'], 1, True),
                         cohort['matched_units'], 'triplets' if len(cohort['expected_arms']) == 3 else 'pairs',
                         cohort['independent_cases'], len(cohort['incomplete_units'])))
        lines.append('')
        lines.append('Models and efforts: ' + '; '.join('%s `%s` (%s)' % (
            role, cohort['settings']['models'][role], cohort['settings']['efforts'][role]) for role in ROLES) + '.')
        lines += ['', '| Condition | Passed / trials | Mean seconds | Mean estimated USD |',
                  '| --- | ---: | ---: | ---: |']
        for arm, item in cohort['arms'].items():
            lines.append('| %s | %d/%d | %s | %s |' % (
                LABELS[arm], item['passed'], item['trials'], formatted(item['mean_seconds'], 1),
                formatted(item['mean_cost_usd'], 4)))
        if cohort['matched_units']:
            chart = link.rsplit('/', 1)[0] + '/cohort-%02d-bars.svg' % index
            lines += ['', '![Matched task outcomes, time, tokens and estimated price](' + chart + ')', '']
    return '\n'.join(lines).rstrip()


def updated_readme(path, output, report):
    original = path.read_bytes().decode('utf-8')
    if original.count(START) != 1 or original.count(END) != 1 or original.index(END) < original.index(START):
        raise ValueError('README requires exactly one ordered evals:overview:start/end marker pair')
    if not output.resolve().is_relative_to(path.resolve().parent):
        raise ValueError('public output must be beneath the README directory when updating its overview')
    link = quote(os.path.relpath(output / 'report.md', path.parent).replace(os.sep, '/'), safe='/')
    before, rest = original.split(START)
    _, after = rest.split(END)
    return before + START + '\n\n' + overview(report, link) + '\n\n' + END + after


def chart_cohort(cohort, output, stem):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        raise ValueError('charting requires matplotlib; install evals/requirements.txt in your eval environment') from None
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False, 'axes.spines.left': False,
                         'axes.edgecolor': '#d4d8df', 'axes.labelcolor': '#343c4b', 'text.color': '#242c3a',
                         'xtick.color': '#343c4b', 'ytick.color': '#687284', 'grid.color': '#e8eaf0',
                         'axes.titleweight': 'bold', 'axes.titlelocation': 'left', 'svg.fonttype': 'none'})
    arms = cohort['expected_arms']
    stats = [cohort['arms'][arm] for arm in arms]
    labels = [LABELS[arm] for arm in arms]
    colors = [COLORS[arm] for arm in arms]
    n = cohort['matched_units']
    title = ('Product capability comparison' if cohort['mode'] == 'comparison'
             else 'Jev ablation · ' + cohort['kind'])
    subtitle = '%d matched %s · %d distinct cases · configuration %s' % (
        n, 'triplets' if len(arms) == 3 else 'pairs', cohort['independent_cases'], cohort['configuration'])
    known = [sum(item['known_tokens'][key] for key in ('input_tokens', 'output_tokens')) / n
             if all(item['known_tokens'][key] is not None for key in ('input_tokens', 'output_tokens')) else None
             for item in stats]
    panels = [([item['pass_rate'] * 100 for item in stats], 'Task outcome pass rate', 'Percent of matched trials', 1),
              ([item['mean_seconds'] for item in stats], 'Wall time per task', 'Seconds, including failed trials', 1),
              (known, 'Recorded tokens per task', 'Input + output tokens; may be partial', 0),
              ([item['mean_cost_usd'] for item in stats], 'Estimated price per task', 'USD at supplied rates', 6)]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8.1))
    fig.subplots_adjust(left=.085, right=.97, bottom=.15, top=.84, hspace=.49, wspace=.26)
    fig.suptitle(title, x=.085, y=.967, ha='left', fontsize=20, fontweight='bold')
    fig.text(.085, .914, subtitle, fontsize=10, color='#687284')
    for panel, (axis, (values, heading, ylabel, digits)) in enumerate(zip(axes.flat, panels)):
        axis.set_title(heading, pad=17, fontsize=12)
        axis.set_ylabel(ylabel, fontsize=9)
        axis.set_xticks(range(len(arms)), labels, fontsize=9)
        axis.set_axisbelow(True)
        axis.yaxis.grid(True)
        maximum = max([value for value in values if value is not None] or [1])
        axis.set_ylim(0, 119 if panel == 0 else max(maximum * 1.30, .0001))
        for index, value in enumerate(values):
            if value is None:
                axis.text(index, axis.get_ylim()[1] * .07, 'Unknown', ha='center', color='#687284')
                continue
            axis.bar(index, value, color=colors[index], width=.58, zorder=3)
            label_height = stats[index]['wilson_95'][1] * 100 if panel == 0 else value
            axis.text(index, label_height + axis.get_ylim()[1] * .035, formatted(value, digits),
                      ha='center', va='bottom', fontsize=10, fontweight='bold')
            if panel == 0:
                bounds = stats[index]['wilson_95']
                axis.errorbar(index, value, yerr=[[value - bounds[0] * 100], [bounds[1] * 100 - value]],
                              color='#293143', capsize=5, linewidth=1.2, zorder=4)
            if panel in (2, 3):
                coverage = stats[index]['usage_complete_trials' if panel == 2 else 'cost_known_trials']
                axis.text(index, -.20, '%d/%d complete' % (coverage, n), transform=axis.get_xaxis_transform(),
                          ha='center', fontsize=8, color='#687284')
    fig.text(.085, .051, 'Failures remain in every matched denominator. Pass-rate whiskers: descriptive 95% Wilson intervals.\n'
             'Repeated trials are not independent cases. Tokens show recorded counts; price is unknown when coverage is incomplete.',
             fontsize=9, color='#687284', linespacing=1.6)
    save_chart(fig, output, stem + '-bars')
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.4))
    fig.subplots_adjust(left=.085, right=.97, bottom=.20, top=.78, wspace=.30)
    fig.suptitle('Quality and resource use', x=.085, y=.96, ha='left', fontsize=20, fontweight='bold')
    fig.text(.085, .873, subtitle, fontsize=10, color='#687284')
    for axis, metric, xlabel in zip(axes, ('mean_seconds', 'mean_cost_usd'),
                                     ('Wall seconds per task', 'Estimated USD per task')):
        axis.set_xlabel(xlabel)
        axis.set_ylabel('Task outcome pass rate (%)')
        axis.set_ylim(-5, 112)
        axis.yaxis.grid(True)
        axis.xaxis.grid(True)
        plotted = False
        for index, (arm, item) in enumerate(zip(arms, stats)):
            if item[metric] is None:
                continue
            plotted = True
            x, y = item[metric], item['pass_rate'] * 100
            axis.scatter(x, y, s=100, color=COLORS[arm], edgecolor='white', linewidth=.8, zorder=3)
            offset = 9 + index * 12 if y < 70 else -12 - index * 12
            axis.annotate(LABELS[arm], (x, y), xytext=(6, offset),
                          textcoords='offset points', fontsize=9, color=COLORS[arm])
        if not plotted:
            axis.text(.5, .5, 'Total estimated price unavailable\nMissing usage or rate coverage',
                      transform=axis.transAxes, ha='center', va='center', color='#687284')
            axis.set_xticks([])
        else:
            maximum = max(item[metric] for item in stats if item[metric] is not None)
            axis.set_xlim(0, maximum * 1.4 if maximum else 1)
    fig.text(.085, .059, 'Each point uses the same matched task units. Means include failures.\n'
             'Exploratory fixture results; paired uncertainty and per-case outcomes appear in the report.',
             fontsize=9, color='#687284', linespacing=1.6)
    save_chart(fig, output, stem + '-tradeoffs')
    plt.close(fig)


def save_chart(fig, output, name):
    fig.savefig(output / (name + '.svg'), metadata={'Date': None}, facecolor='white')
    fig.savefig(output / (name + '.png'), dpi=160, facecolor='white')


def public_provenance(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    result = {}
    for key, size in (('source_commit', 40), ('source_diff_sha256', 64), ('corpus_sha256', 64)):
        if key in manifest:
            value = manifest[key]
            if not isinstance(value, str) or not re.fullmatch('[a-fA-F0-9]{%d}' % size, value):
                raise ValueError('invalid public provenance checksum')
            result[key] = value
    if 'created_at' in manifest:
        try:
            created = datetime.fromisoformat(manifest['created_at'].replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid run created_at timestamp') from None
        if created.tzinfo is None:
            raise ValueError('run created_at must include a timezone')
        result['created_at'] = created.isoformat()
    if 'native_version' in manifest:
        version = manifest['native_version']
        if not isinstance(version, str) or not re.fullmatch(r'codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+.][a-zA-Z0-9.-]+)?', version):
            raise ValueError('native_version must be a public codex-cli version label')
        result['native_version'] = version
    for key in ('repetitions', 'seed'):
        if key in manifest:
            result[key] = number(manifest[key], key, integer=True)
            if key == 'repetitions' and result[key] < 1:
                raise ValueError('repetitions must be positive')
    if 'eval_sources' in manifest:
        sources = manifest['eval_sources']
        if not isinstance(sources, dict):
            raise ValueError('eval_sources must map file basenames to SHA256 checksums')
        for name, checksum in sources.items():
            if (not isinstance(name, str) or name in ('.', '..')
                    or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,128}', name)
                    or not isinstance(checksum, str) or not re.fullmatch('[a-fA-F0-9]{64}', checksum)):
                raise ValueError('eval_sources accepts only file basenames and SHA256 checksums')
        result['eval_sources'] = sources
    return result


def report_markdown(report, *, charts=True):
    lines = ['# Cerebro evaluation results', '',
             'These results measure selected small CSV fixtures. They describe the recorded run; '
             'they are not a broad model benchmark or evidence of a statistically significant product advantage.', '',
             'Product comparisons grade the same portable task outcome for every arm. Jev calibration and steering '
             'ablations are separate; scripted protocol checks measure transport and enforcement, not model effectiveness.', '',
             'Review calibration requires the expected validity, usefulness and review-disposition labels together. '
             'The diagnostics also show each label separately: an incorrect action label does not by itself mean '
             'the parent missed a defect or followed a malicious instruction.', '',
             'Only complete expected pairs or triplets with identical case, repetition, and full model/effort settings '
             'enter comparative success, time, token, and cost aggregates. A failed or errored trial remains a failure '
             'in its matched unit; its elapsed time and recorded usage remain included. Missing arms are listed as incomplete.', '',
             'Pass-rate intervals are descriptive 95%% Wilson reference intervals on trials, conditional on selected tasks. '
             'They do not account for dependence between repetitions. Paired deltas use a deterministic percentile bootstrap '
             'resampling whole cases, retaining their repetitions together, only at %d or more distinct cases. '
             'Below that threshold paired intervals are unavailable; no significance claim is made.' % BOOTSTRAP_MIN_CASES, '',
             '**Recorded trials:** %d. **Errors:** %d. [Sanitized trial data](trials.json) · '
             '[Aggregate statistics](aggregate.json).' % (report['total_trials'], report['total_errors']), '']
    provenance = report.get('provenance', {})
    if provenance:
        lines += ['## Run provenance', '', '| Recorded field | Value |', '| --- | --- |']
        for key, value in provenance.items():
            if key != 'eval_sources':
                lines.append('| %s | `%s` |' % (key, value))
        lines += ['', 'Per-file SHA256 source fingerprints are in [aggregate.json](aggregate.json). '
                  'To rerun, use `evals/run.py` with the recorded case IDs, repetition count, seed, native CLI version, '
                  'and the role model/effort and Jev settings below. `evals/README.md` documents the configuration '
                  'and runner flags. Use a new output directory. The private Jev endpoint is represented by its '
                  'SHA256 fingerprint; its URL and credentials are not published.', '']
    prices = report['price_sheet']
    if prices:
        lines += ['Prices are **estimates at the supplied API reference rates**, not an account bill. '
                  'Rate date: %s; [rate source](%s). %s' % (
                      prices['as_of'], quote(prices['source'], safe=':/.-_'), markdown(prices['basis'])), '',
                  '| Model | Input / 1M | Cached input / 1M | Cache-write input / 1M | Output / 1M |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for model, rates in sorted(prices['models'].items()):
            lines.append('| %s | %s | %s | %s | %s |' % (model, *(formatted(rates.get(key), 4) for key in (
                'input_per_million', 'cached_input_per_million', 'cache_write_input_per_million', 'output_per_million'))))
        lines.append('')
    else:
        lines += ['No rate sheet was supplied. Estimated dollar costs are unknown.', '']
    lines += ['Input counts include cached and cache-write tokens; those columns are subsets, not extra input. '
              'Known token totals can be partial. No missing worker, token count, or price is treated as zero. '
              'Total estimated cost requires complete worker coverage and applicable rates; unknown cache subdivisions '
              'are priceable only when all input rates are equal.', '']
    for index, cohort in enumerate(report['cohorts'], 1):
        arms = cohort['expected_arms']
        title = ('Product comparison' if cohort['mode'] == 'comparison' else
                 'Protocol checks' if cohort['mode'] == 'protocol' else
                 'Jev ablation' if len(arms) > 1 else 'Single-condition live checks')
        lines += ['## %s · %s · configuration %s' % (title, cohort['kind'], cohort['configuration']), '',
                  '%d matched units across %d distinct cases; %d incomplete units (%d recorded trials excluded). '
                  '%d errors among all %d recorded trials in this cohort.' % (
                      cohort['matched_units'], cohort['independent_cases'], len(cohort['incomplete_units']),
                      cohort['excluded_trials'], cohort['all_errors'], cohort['all_trials']), '',
                  '| Role | Model | Effort |', '| --- | --- | --- |']
        for role in ROLES:
            lines.append('| %s | %s | %s |' % (role, cohort['settings']['models'][role], cohort['settings']['efforts'][role]))
        lines += ['', 'Jev model: `%s`; confidence threshold: %s; timeout per stage: %ss. '
                  'Endpoint fingerprint: `%s`.' % (cohort['settings']['jev_model'], cohort['settings']['jev_confidence'],
                  cohort['settings']['timeout'], cohort['settings']['jev_endpoint_sha256'])]
        lines += ['', '| Arm | Passed / trials | Pass rate | Wilson 95% | Seconds / task | Errors |',
                  '| --- | ---: | ---: | ---: | ---: | ---: |']
        for arm, item in cohort['arms'].items():
            lines.append('| %s | %d/%d | %s%% | %s%% | %s | %d |' % (
                LABELS[arm], item['passed'], item['trials'],
                formatted(item['pass_rate'] * 100 if item['pass_rate'] is not None else None, 1),
                interval(item['wilson_95'], 100), formatted(item['mean_seconds']), item['errors']))
        lines += ['', '| Arm | Known input | Cached subset | Cache-write subset | Known output | Complete usage trials | Estimated USD / task | Priced trials |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for arm, item in cohort['arms'].items():
            lines.append('| %s | %s | %s | %s | %s | %d/%d | %s | %d/%d |' % (
                LABELS[arm], *(formatted(item['known_tokens'][key], 0) for key in TOKEN_KEYS),
                item['usage_complete_trials'], item['trials'], formatted(item['mean_cost_usd'], 6),
                item['cost_known_trials'], item['trials']))
        if cohort['deltas']:
            lines += ['', 'Deltas are **after − before**; positive seconds or USD mean additional resource use. '
                      'Intervals in parentheses are paired case-bootstrap 95% intervals.', '',
                      '| Comparison | Δ pass (percentage points) | Improved / regressed / tied | Δ seconds / task | Δ estimated USD / task |',
                      '| --- | ---: | ---: | ---: | ---: |']
            for delta in cohort['deltas']:
                cells = [formatted(delta[key], 6 if 'usd' in key else 1, True)
                         + ' (' + interval(delta['cluster_bootstrap_95'][key], digits=6 if 'usd' in key else 1) + ')'
                         for key in ('pass_rate_delta_pp', 'mean_seconds_delta', 'mean_cost_usd_delta')]
                lines.append('| %s → %s | %s | %d / %d / %d | %s | %s |' % (
                    LABELS[delta['before']], LABELS[delta['after']], cells[0], delta['improved'],
                    delta['regressed'], delta['tied'], cells[1], cells[2]))
        if charts and cohort['mode'] != 'protocol' and len(arms) > 1 and cohort['matched_units']:
            stem = 'cohort-%02d' % index
            lines += ['', '![Matched quality, time, recorded tokens and estimated price](%s-bars.svg)' % stem, '',
                      '[PNG](%s-bars.png) · [SVG](%s-bars.svg)' % (stem, stem), '',
                      '![Quality versus wall time and estimated price](%s-tradeoffs.svg)' % stem, '',
                      '[PNG](%s-tradeoffs.png) · [SVG](%s-tradeoffs.svg)' % (stem, stem)]
        lines += ['', '### Per-case outcomes and overhead', '',
                  'Each cell shows **passed / trials; mean seconds; mean estimated USD** on the same matched units.', '',
                  '| Case / capability | ' + ' | '.join(LABELS[arm] for arm in arms) + ' |',
                  '| --- | ' + ' | '.join('---:' for _ in arms) + ' |']
        for case in cohort['capabilities']:
            cells = ['%d/%d; %ss; $%s' % (item['passed'], item['trials'], formatted(item['mean_seconds'], 1),
                                        formatted(item['mean_cost_usd'], 6)) for item in case['arms'].values()]
            lines.append('| %s / %s | %s |' % (case['case'], case['kind'], ' | '.join(cells)))
        if cohort['incomplete_units']:
            lines += ['', '| Incomplete case | Repetition (zero-based) | Present arms | Missing arms |',
                      '| --- | ---: | --- | --- |']
            for unit in cohort['incomplete_units']:
                lines.append('| %s | %d | %s | %s |' % (unit['case'], unit['repeat'],
                    ', '.join(LABELS[arm] for arm in unit['present_arms']), ', '.join(LABELS[arm] for arm in unit['missing_arms'])))
        workers = [(arm, worker) for arm, item in cohort['arms'].items() for worker in item['usage_by_role']]
        if workers:
            lines += ['', '### Recorded provider and role usage', '',
                      'These totals cover recorded workers only. Each token count includes its **known entries / recorded entries**. '
                      'Completeness of all workers for a trial is reported above.', '',
                      '| Arm / provider / role / model | Input | Cached | Cache-write | Output | Complete entries | Estimated USD |',
                      '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
            for arm, worker in workers:
                counts = ['%s (%d/%d)' % (formatted(worker['known_tokens'][key], 0),
                          worker['token_known_entries'][key], worker['entries']) for key in TOKEN_KEYS]
                lines.append('| %s / %s / %s / %s | %s | %d/%d | %s |' % (
                    LABELS[arm], worker['provider'], worker['role'], worker['model'], ' | '.join(counts),
                    worker['complete_entries'], worker['entries'], formatted(worker['estimated_cost_usd'], 6)))
        metric_names = sorted({key for item in cohort['arms'].values() for key in item['metrics']})
        if metric_names:
            lines += ['', '### Recorded diagnostics', '', 'Cells show **sum / observed trials (mean)**; missing observations stay missing.', '',
                      '| Metric | ' + ' | '.join(LABELS[arm] for arm in arms) + ' |',
                      '| --- | ' + ' | '.join('---:' for _ in arms) + ' |']
            for key in metric_names:
                cells = []
                for item in cohort['arms'].values():
                    metric = item['metrics'].get(key)
                    cells.append('unknown' if metric is None else '%s / %d (%s)' % (
                        formatted(metric['sum'], 1), metric['n'], formatted(metric['mean'], 2)))
                lines.append('| %s | %s |' % (key, ' | '.join(cells)))
        lines.append('')
    return '\n'.join(lines)


def publish(directory, output, prices=None, readme=None):
    directory, output = Path(directory), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('publication output already exists; choose a new directory')
    document = json.loads((directory / 'results.json').read_text())
    report = summarize(document, prices)
    report['published_at'] = datetime.now(timezone.utc).isoformat()
    report['provenance'] = public_provenance(document.get('manifest', {}))
    readme_path = Path(readme) if readme is not None else None
    readme_text = updated_readme(readme_path, output, report) if readme_path is not None else None
    output.mkdir(parents=True, exist_ok=False)
    try:
        for index, cohort in enumerate(report['cohorts'], 1):
            if cohort['mode'] != 'protocol' and len(cohort['expected_arms']) > 1 and cohort['matched_units']:
                chart_cohort(cohort, output, 'cohort-%02d' % index)
        (output / 'report.md').write_text(report_markdown(report))
        trials = report.pop('trials')
        (output / 'trials.json').write_text(json.dumps(trials, indent=2, allow_nan=False) + '\n')
        (output / 'aggregate.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    except BaseException:
        shutil.rmtree(output)
        raise
    if readme_path is not None:
        readme_path.write_text(readme_text)
    return output / 'report.md'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path, help='private run containing results.json')
    parser.add_argument('--out', required=True, type=Path, help='new public directory; never overwritten')
    parser.add_argument('--prices', type=Path, help='public JSON rate sheet with as_of, source, basis and models')
    parser.add_argument('--readme', type=Path, help='replace only the existing evals:overview marker block')
    args = parser.parse_args()
    try:
        prices = json.loads(args.prices.read_text()) if args.prices else None
        result = publish(args.run_dir, args.out, prices, args.readme)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print('Published report: ' + str(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
