# Lease queue challenge: no demonstrated advantage

This exploratory task tested a JSON-backed job queue with simultaneous workers,
lease expiry, stale owners, independent snapshots and restart persistence. The
requirements and grader were frozen in `e376652` before inference. All three
conditions received the same initial repository and requirements, with a
300-second parent deadline. Bare Terra used medium effort; Cerebro used Luna low
for implementation and Terra medium for supervision and independent review.

| Condition | Delivery within deadline | Recorded seconds | Saved code after shutdown | Estimated USD |
| --- | --- | ---: | --- | ---: |
| Bare Terra | Passed | 151.5 | All behavioral checks passed; scope preserved | 0.1473 |
| Cerebro without Jev | Timed out | 301.6 | Behavioral checks passed; supplied test file changed | Unknown |
| Cerebro with Jev | Timed out | 301.5 | Required return behavior failed; scope preserved | Unknown |

Elapsed time includes setup and cleanup around the parent deadline. Interrupted
workers have incomplete usage evidence, so their total prices remain unknown.
Recorded input plus output tokens were 167,028 for bare Terra, 507,831 for
Cerebro without Jev and 759,009 for Cerebro with Jev; the latter two are partial.
These are API reference estimates, not account billing.

The original [report and charts](report.md) retain both deadline failures. The
runner does not grade code when the parent times out, so its original functional
diagnostics are unmeasured. After all trials ended, the same frozen grader was
run on every saved checkout, without changing source. Those supplemental results
are in [functional checks](supplemental-functional-checks.json). They do not
retroactively turn an unfinished delivery into a pass. The hidden sequence stops
at an unexpected exception; subsequent false entries do not establish separate
independent defects.

## What the traces showed

In the Jev condition, the supervisor replaced the detailed task with a shorter
packet referring to “specified signatures and return/error semantics” without
including those semantics. The original requirement explicitly said busy or
completed claims return `None`, and invalid completion/retry return `False`.
Neither the child prompts nor Jev's recorded requests retained those clauses.
The implementor used `ValueError` instead. Independent review missed this
mismatch and requested an additional finite-number overflow check. Jev assessed
that finding as uncertain in validity, useful and proportionate, with incomplete
context. The supervisor started that correction before the deadline expired.

[Direct probes of the saved code](supplemental-return-contract.json) confirm
that busy claim, wrong-token completion and wrong-token retry all raise
`ValueError` in the Jev condition. Bare Terra and the condition without Jev return
the required values. This is a concrete loss of task requirements during
delegation; this single run does not establish that Jev caused the difference.

Jev made 22 implementation assessments, published six notices and assessed one
review finding. There were no observer/provider failures and no recorded accepted
native steering calls. The supervisor acknowledged concerns through wait
dispositions; those acknowledgments alone do not steer an implementor. Completed
Jev requests cost an estimated $0.0075.

Without Jev, review raised numeric-identifier persistence, which the task did not
explicitly define. The supervisor started a correction that added regression
tests to the protected `test_jobs.py` instead of the authorized optional
`test_regressions.py`. Existing tests were not weakened or removed, but the file
preservation requirement was violated. The second review had not finished when
the deadline expired.

## Implication

This case did not expose the sought bare-agent weakness. It exposed a Cerebro
failure: downstream agents can all agree on a plan after important original
requirements disappear. The next change should preserve the original task for
implementation, review and Jev independently of the supervisor's plan, and keep
review corrections tied to that original task. The case should then be rerun
unchanged. A later representative set of repository tasks is needed to measure
whether any observed benefit generalizes; this selected case is not such proof.
