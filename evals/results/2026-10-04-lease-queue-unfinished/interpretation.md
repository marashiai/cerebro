# Stall removed; a proportionality failure remains

The unchanged lease-queue case was rerun at `a2a91cb`, after two fixes. A stage
now ends when its native turn ends, and tool calls still running at that point
are returned to the supervisor as unfinished. Jev now wakes the supervisor only
for a cited concern with a concrete reason at or above the confidence threshold,
once per event and reason. The case, grader, models, efforts, ordering seed and
300-second parent deadline match the
[previous rerun](../2026-10-04-lease-queue-context/interpretation.md). Bare Terra
used medium effort; Cerebro used Luna low for implementation and Terra medium for
supervision and review.

| Condition | Delivery | Recorded seconds | Saved code after shutdown | Recorded input + output tokens | Estimated USD |
| --- | --- | ---: | --- | ---: | ---: |
| Bare Terra | Passed | 125.6 | 19/19 behavioral checks; scope preserved | 148,638 | 0.1481 |
| Cerebro without Jev | Timed out | 301.5 | 19/19 behavioral checks; **supplied tests modified** | 604,240, partial | Unknown |
| Cerebro with Jev | Passed | 145.6 | 19/19 behavioral checks; scope preserved | 315,946 | 0.1627 |

[Report and charts](report.md) keep the timeout as a failure. Elapsed time
includes setup and cleanup. Interrupted parent usage is incomplete, so the price
of the no-Jev arm is unknown, not zero. Prices use recorded API reference rates
and are not account bills. [Supplemental checks](supplemental-functional-checks.json)
rerun the frozen grader on every saved checkout after all trials stopped. They
describe saved code and do not change delivery outcomes.

## The stall did not recur, and this run did not test its fix

No reviewer left a command running at the end of its turn, so no
unfinished-tool receipt was produced and the new path was not exercised live.
Offline native-transport tests cover it for Codex, Claude and Pi. They hang
against the previous controller and pass with the fix. Both completed review
handoffs reached the supervisor immediately. No eval child processes remained
after the run.

## Why the no-Jev arm still timed out

| Phase | Seconds into the run |
| --- | ---: |
| Supervisor plan | 0–23 |
| Implementation | 23–70 |
| Review | 70–188 |
| Supervisor adjudication | 188–204 |
| Correction implementation | 204–248 |
| Correction review, interrupted by the deadline | 248–301 |

The first review reported two findings. F1 (medium) said UUID4 tokens only
probabilistically satisfy "a later claim never reuses a token". The reviewer
established this by patching `uuid.uuid4` to return a constant. F2 (low) said
the implementation's transaction helper rewrote unchanged JSON after rejected
claims and completions, which the reviewer read as a mutation. Both readings
have a literal basis in the task text. Neither is detected by the frozen grader,
and both passing arms also use `uuid.uuid4().hex`. The supervisor accepted both
findings and started a full correction cycle, which cannot fit the remaining
budget.

The correction implementor then added its two new tests to the supplied
`test_jobs.py`, which both the original request and the correction packet
forbid. It also replaced random tokens with predictable `identifier:generation`
strings and persisted an extra `_claim_generation` field. The interrupted second
review never assessed these changes. This arm's saved code passes every
behavioral check but fails the scope check: the correction made the result
worse, not only slower.

## Why the Jev arm passed

Its implementation returned before writing on rejected transitions, so the F2
pattern was absent. Its reviewer did not raise the UUID question and reported no
findings. Jev's review assessment marked the clean summary as useful but its
validity uncertain, since a bounded diff cannot prove the absence of defects.
The supervisor accepted the review after about 11 seconds, including that
assessment.

This success should not be attributed to Jev. Jev made four implementation
classifications and woke the supervisor zero times, so no steering occurred. The
two arms differ mainly in implementation shape and reviewer judgment. Under the
previous wake policy, two of the four classifications would have been published:
low-confidence `uncertain` results, one with reason `none` and one
`unsupported_assumption` at 0.58. Recorded Jev cost was about $0.0013.

## What this shows

Fixing the lifecycle stall made supervised delivery possible within the budget,
but one stochastic trial per arm still finds no quality advantage over bare
Terra. The successful supervised run took 20 seconds longer and cost about 10%
more than bare. The failed one shows the main remaining risk: a reviewer's
low-impact, literal-reading findings can trigger a full correction cycle that
costs more than the original work and can introduce real scope violations.

## Follow-up changes after this run

An independent review of the Jev change, completed during this run, found two
gaps. First, a later user clarification could not re-raise a concern on an
already acknowledged event. Second, no Jev reason covered skipped required
verification, so that concern could not wake the supervisor. Commit `c70a285`
fixes both after the run. Neither case occurred in this trial; the Jev log shows
no affected classification.

The proportionality failure above is not fixed. Hypotheses to test, not
conclusions:

- Before starting a correction cycle, the supervisor could weigh each finding's
  evidenced impact against the cost of another implementation and review. Jev's
  per-finding proportionality assessment is meant to inform that decision, but
  the Jev arm's review had no findings to assess in this trial.
- Correction rounds may need the reviewer to re-check scope rules explicitly,
  since the first correction broke one.
- Lower review effort may cut the reasoning-bound review time (70–118 seconds
  here), but it must be measured as an explicitly labeled configuration change.
