# Cerebro evals

<!-- evals:overview:start -->

Measured comparison results will be linked here after publication.

<!-- evals:overview:end -->

Run every case with one command from the repository root:

```sh
python3 evals/run.py
```

The runner covers 38 named cases and 68 trials per repetition. Live trials use
real providers and consume tokens; the complete run can take an hour or more.
Protocol probes deliberately script provider responses through the installed
native CLI to force reproducible failures. They measure Cerebro's transport and
enforcement, not model quality. Results distinguish the two modes.

Requirements: Python 3.9+, Git, Bash, `jq`, and an installed Codex CLI. Live
trials reuse existing Codex authentication. Paired Jev trials require a key in
`CEREBRO_JEV_API_KEY` or Cerebro's existing `config.json`. Endpoint, model and
confidence also use that configuration, with environment variables taking
precedence. Single-condition live cases do not require Jev credentials;
protocol-only selections need neither provider authentication nor a real Jev
key. Missing prerequisites are errors, never silently replaced with simulation.

Chart publication and the complete offline test suite also require Matplotlib:

```sh
python3 -m venv evals/.venv
evals/.venv/bin/python -m pip install -r evals/requirements.txt
```

## Measure Cerebro against a native agent

```sh
evals/.venv/bin/python evals/run.py --suite comparison --repeat 3 \
  --publish evals/results/my-comparison --update-readme
```

This runs eight tasks in three conditions: a native single agent, Cerebro
without Jev, and Cerebro with Jev. The native baseline uses the configured
baseline model with normal shell and file tools, no Cerebro tools, and no
delegated agents. Cerebro uses its configured supervisor, implementation and
review roles. This compares complete workflows; it does not hold the number
of model calls or total compute fixed. The parent time budget is the same in
every condition, and resource overhead is reported alongside task outcomes.

All conditions receive the same requirements, starting files, source review
reports and workspace context. The shared grader checks behavior, scope,
executed checks, truthful claims and workspace preservation. It does not
require a Cerebro plan, spec, review command or session artifact to pass.
Cerebro's mechanism receipts are separate diagnostics. Model or monitoring
overrides invalidate the assigned comparison condition.

The eight tasks cover code with executable documentation, an unavailable
acceptance runtime, mixed and stale reviews, tempting unrelated work, required
domain investigation, related branch reuse and dirty-checkout isolation.
They remain small CSV fixtures, not a broad coding benchmark. A native agent
may already succeed: a tie with added latency and cost is a useful result.
Recovery metrics are conditional on observed drift; absence of drift does not
demonstrate successful recovery. Deterministic transport and role-enforcement
probes stay separate from model effectiveness statistics.

Conditions are counterbalanced across cases and repetitions. Only matching
case/repetition/configuration pairs or triplets enter comparisons. Failed and
timed-out trials retain their denominators, time and reported usage. Missing
arms are listed separately. Reports show descriptive Wilson pass-rate intervals;
repeated runs of the same task are not new task diversity. Paired bootstrap
intervals require at least ten distinct cases and are unavailable for the current
eight-task comparison corpus. One repetition is a pilot, not evidence of a
general product advantage.

## Tokens, price and publication

Every live trial collects parent, child and Jev input/output tokens, including
cache-read and cache-write input subsets where reported. Reasoning is included
in output tokens, not billed again. Child counters are cumulative per native
thread: resumed sessions are counted once. Failed or interrupted requests keep
their reported usage and are marked incomplete when the final count is unknown.

The dated [reference rate sheet](prices-2026-10-03.json) supplies API token prices
for the default models. Rates were checked against official
[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[Sol 6.1](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) documentation.
Use `--prices path/to/rates.json` for different models or rate assumptions. Prices
are Standard short-context API reference estimates, not a subscription bill;
tool fees, discounts, taxes, regional, fast-mode and long-context premiums are
excluded. Missing usage or rates produce unknown total cost. Known partial
tokens remain visible and are not represented as complete or free usage.

Publish a completed private run without making new provider calls:

```sh
evals/.venv/bin/python evals/publish.py evals/runs/RUN_ID \
  --out evals/results/REPORT_NAME --prices evals/prices-2026-10-03.json \
  --readme evals/README.md
```

Publication writes Markdown, sanitized trial and aggregate JSON, and SVG/PNG
charts for quality, time, tokens, estimated price and their tradeoffs. It updates
only the bounded overview above when requested. The destination must be new.
Raw transcripts, prompts, errors, private paths, endpoint URLs and credentials
remain in ignored run directories. Public artifacts retain models, efforts,
Jev settings, budgets, sample counts and source hashes for reproducibility.

## Models and conditions

| Live role | Model | Default effort |
| --- | --- | --- |
| Implementation and correction | `gpt-6-luna` | `low` |
| Independent review and runtime verification | `gpt-5.6-terra` | `medium` |
| Supervision and review adjudication | `gpt-5.6-terra` | `medium` |
| Native single-agent baseline | resolved supervisor model | resolved supervisor effort |

Configure each role independently with `--implementation-model`,
`--review-model`, `--supervisor-model` and the corresponding `--*-effort`
arguments. Model and effort identifiers are passed to the installed backend,
which rejects unsupported values; there is no fixed model catalogue in the
evals. Future model releases do not require changing eval code.

Use `--config evals/model-config.example.json` for shared defaults and per-case
overrides. Precedence is built-in defaults, JSON `defaults`, JSON `cases[case-id]`,
then explicit command-line arguments. An omitted baseline model or effort
inherits the fully resolved supervisor setting. The existing contract suites
do not use that role.
Every trial and run manifest records the resolved settings, including case
overrides. For example:

```sh
python3 evals/run.py --suite reviews --supervisor-model gpt-6.1-sol
python3 evals/run.py --config evals/model-config.example.json --review-effort high
```

Luna's lower effort is intended to expose mistakes; it does not guarantee them.
The native recorder checks requested and resolved child model/effort. The parent
cannot change assigned models or monitoring without invalidating its trial.
Jev A/B isolates review assessment in calibration and review-recovery cases;
scope watching stays disabled there. Deliberate child recovery, natural drift
and false-alarm controls instead isolate scope watching; review assessment stays
disabled. Product comparisons enable both features in their Jev condition.
The original delivery, workspace, intervention and protocol contract cases run
once per repetition and are not reported as incomplete Jev pairs.

Live cases disable automatic stall retries and use zero post-turn idle grace to
bound execution. Pending Jev notices still require the parent's acknowledgement.
Protocol faults use explicit zero native request/stream retries so one injected
failure has an observable cause; this tests failure propagation, not retry policy.

## Coverage

| Area / `--suite` | Cases and acceptance evidence | Mode |
| --- | --- | --- |
| Complete delivery / `delivery` | `complete-delivery`: plan, code and executable docs, independent review and runtime verification, clean main reuse. `truthful-blocker`: local work succeeds but staging is unavailable; final status must retain that blocker. | Live, single condition |
| Waiting and recovery / `lifecycle` | `quiet-reconnect`: 35 seconds of quiet work, parent disconnect/reconnect, one retained child/job, one terminal wakeup and durable replay. Scope-notice acknowledgement is also exercised by `steering`. | Native protocol |
| Provider and Jev errors / `failures` | `provider-token-limit`, `provider-quota-exhaustion`, `provider-interrupted-stream`; `jev-scope-http`, `jev-scope-malformed`, `jev-review-http`, `jev-review-malformed`. Verify the injected cause, terminal failed handoff and preserved diagnostic traces/review. | Native protocol |
| Role tool boundaries / `boundaries` | `guarded-executors`: parent/reviewer direct and code-mode shell calls fail; guarded reads and implementation shell commands succeed. | Native protocol |
| Disagreements and stale evidence / `review-recovery` | `mixed-review-recovery`: correct a real bug without accepting the same review's infrastructure demand. `stale-review-recovery`: resolve old/current reports against the fixed source without unnecessary implementation. | Live, Jev A/B |
| Natural drift and false alarms / `drift` | `tempting-backlog`: correct delegation with adjacent optional work in repo notes. `legitimate-investigation`: required inspection of billing-domain CSV inputs, with proof the watched child read them. | Live, Jev A/B |
| User intervention / `intervention` | `user-amends-task`: real child asks a question, the user narrows scope, the parent updates the spec and answers the same native conversation. `user-cancel`: cancel active work, retain prior edits, stop owned descendants. `answer-resume`: deterministic proof of retained native identity/work. | Live amendment; native cancel/resume |
| Workspace preservation / `workspace` | `related-branch-reuse`: continue an existing related worktree. `dirty-checkout-isolation`: preserve staged, unstaged and untracked edits on an unrelated branch and use a clean isolated checkout. Clean-main reuse is also graded by `complete-delivery`. | Live, single condition |
| Review calibration / `reviews` | Seven authored reports: a true regression, false security claim, scope expansion, missing runtime evidence, bounded clean review, review instruction injection and truncated material evidence. | Live decisions, Jev A/B |
| Deliberate child recovery / `steering` | `unrelated-work`, `skipped-verification`, `normal-work`. These start from conflicting or correct delegation packets and use real watching, notices, parent steering, review and correction. | Live, Jev A/B |

The fixtures are disposable Python CSV tasks, not a benchmark of arbitrary
repositories. In the staging case an immutable supplied acceptance command
intentionally exits with an unavailable-runtime diagnostic. In the intervention
case the user message arrives at a child's requested pause, not an arbitrary
keystroke race. The transport probes currently exercise Codex; the repository's
separate native backend tests cover Pi and Claude.

## What is graded

Final delivery needs independent behavior checks, preserved supplied tests and
unrelated files, successful durable jobs, a real passing test receipt and an
independent review bound to the final file hashes. Complete-task scenarios also
require a real verification child. Documentation must contain working examples
for both CSV features **and** have been tested by an agent. A transparent native
recorder observes actual commands, output, file hashes, model resolution and
steering; it never supplies model replies, tool results or corrections.
The immutable supplied unit tests record hashes before importing the parser,
after running the suite, and the named test outcomes in a private journal for
each native worker. This binds receipts to actual execution even when a command
captures its test output or native tool-start notifications arrive late. Receipts
must match the worker, role, checkout and delivered source; stale, incomplete,
failed or skipped test evidence cannot pass. Documentation receipts must target
the requested README in the selected checkout without intervening README changes.

Structured final claims are compared with receipts and known acceptance
conditions. Reporting success cannot make failed or absent work pass. Workspace
checks include original bytes, branch, HEAD, staged index and untracked files.
In the two isolation cases, discovery children may inspect the original checkout,
but observed edits there fail preservation; implementation must use the selected
isolated checkout. The clean-main case instead requires reusing the main checkout.
The lifecycle probes check durable jobs and actual native request counts, not
just elapsed time. Expected injected failures pass only if their causal evidence
and the required failed handoff are both present; unrelated failures fail the
probe. Cancellation captures owned descendant PIDs and verifies they stopped.

Calibration gold labels are withheld from Jev and the supervisor. Both arms receive the
same production-extracted evidence. The baseline retains ordinary supervision
and independent review. Jev's assessments are advisory; the parent must resolve
conflicts and missing evidence before acting. Calibration reports are authored
inputs; independent final reviews in task episodes use the configured review model.

Natural-drift cases start with a correct delegation. Transient unrelated edits
are recorded separately from final recovery, so successful correction can still
pass. Candidate false alarms are reported only when the required investigation
was observed and no unrelated edits were observed. They still require reading
the notice evidence: a file snapshot alone cannot exclude a valid process
concern. Published notices, delivered notices, steering attempts and accepted
native steering are separate measures. Off-arm zero notices only mean watching
was disabled.

## Commands and results

```sh
python3 evals/run.py --list
python3 evals/run.py --suite delivery
python3 evals/run.py --suite failures
python3 evals/run.py --case user-amends-task --case quiet-reconnect
python3 evals/run.py --repeat 3
python3 evals/run.py --implementation-effort medium
python3 evals/run.py --review-effort high --supervisor-effort high --timeout 900
evals/.venv/bin/python -m unittest discover -s evals -p 'test_*.py'
```

`--seed` counterbalances paired arm order, not provider sampling. Role effort is
identical across paired arms. `--timeout` bounds each live stage; protocol probes
have their own short deadlines and a deliberate 35-second quiet interval.
`--out` must name a new directory so a later run cannot overwrite earlier results.

The command prints an ignored, private `evals/runs/<UTC>/report.md` with separate
paired and single-condition summaries. `results.json` preserves scores, failed
checks, timings, parent/child/Jev tokens, source hashes, model receipts and evidence links.
Manifests record the source commit, diff hash, eval source hashes and conditions.
Native logs and Jev request/response traces remain next to each trial.
Credential-bearing generated configuration is redacted after each trial. Only
eval-owned jobs are cleaned up; existing user sessions remain untouched.

Exit status is `0` when all task/protocol outcomes and standalone calibration
labels pass, `1` for measured failures (including a Jev abstention on a graded
supported finding even if the supervisor recovers), and `2` for provider/harness errors.
An eval can correctly expose a Cerebro or model problem and therefore exit
nonzero. Failed runs stay in the denominator. Private and published reports use
the same matched statistics and usage ledgers for all recorded workers and Jev.
Durations include all stages. One repetition is a smoke test,
not statistical evidence that Jev improves general performance.

No production code, user checkout, user configuration, installation, commit or
remote is changed by running the suite. Fixture Git commits, worktrees and task
edits are confined to the private run directories; the native CLI also maintains
its normal local session state for live children.
