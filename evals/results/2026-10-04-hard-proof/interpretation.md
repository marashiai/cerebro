# Can Cerebro with Jev beat a bare agent?

This focused run compares Cerebro with Jev against two bare agents on two new
tasks, three repetitions each, at `8c794bb`. Cerebro used Luna low for
implementation and Terra medium for supervision and review. The bare arms ran
Luna low alone and Terra medium alone. Every arm had the same frozen task text,
grader and 600-second deadline.

Both tasks were designed and frozen before inference. Bare Luna was then
screened once per task: it passed inventory reservations and failed patch apply.
Both tasks were kept: inventory as a control where the implementor often
succeeds, and patch apply as a task where it does not.

| Arm | Delivered | Saved code correct | Mean seconds | Mean estimated USD |
| --- | ---: | ---: | ---: | ---: |
| Bare Luna | 2/6 | 2/6 | 65 | 0.004 |
| Bare Terra | 5/6 | 5/6 | 152 | 0.178 |
| Cerebro with Jev | 3/6 | 5/6 | 474 | 0.428 (5 of 6 priced) |

"Saved code correct" means the frozen grader's behavioral and scope checks pass
on the checkout left at the end; it ignores the delivery report and the
deadline. [Report and charts](report.md) keep every failure.

## Per task

| Task | Bare Luna | Bare Terra | Cerebro with Jev |
| --- | --- | --- | --- |
| Inventory reservations | 2/3; one run missed the validation-precedence rule | 2/3; one run missed the same rule | 2/3 delivered in 367 and 472 s; the third ended blocked after an accepted review finding broke `idempotent_same` |
| Patch apply | 0/3; each missed 5 to 6 rules, including reverse round trip, offset carry-over and end-of-file newlines | 3/3 in 98 to 218 s | 1/3 delivered in 401 s; one reported a remaining edge case as blocked although the code passed every check; one timed out at 601 s with correct saved code |

## What the evidence shows

Supervision rescued Luna's quality. On patch apply, Cerebro's saved code passed
every hidden check in all three trials, where bare Luna failed every trial. The
reviewers found the defects Luna left (line endings, carried offsets, missing
newlines), and narrow corrections fixed them.

Cerebro did not beat bare Terra. Terra alone delivered 5/6 in about a third of
the time and less than half the estimated cost. Cerebro matched Terra's 5/6
correct saved code but delivered only 3/6.

The delivery losses come from review loops that do not converge. Every Cerebro
trial ran at least one correction, and four of six ran two. Each correction
review, although scoped to the changes since the earlier review, found a new
edge case in the corrected code. Examples:

- `Fraction` TTL values that do not serialize;
- `10**1000` TTLs combined with a float `now`;
- `Infinity` in the JSON log;
- interior no-newline markers.

Most of these are outside the graded rules. The supervisor accepted them as
contract issues until the eval's two-correction limit or the deadline ended the
run. In one inventory trial, the reviewer read "reserve(now) first expires" as
applying to idempotent repeats. That contradicts the stated "no new event" rule.
The accepted correction broke a passing check. The task wording invites that
reading, which is a limitation of this fixture.

## Jev

Jev made 6 to 15 classifications per stage and woke the supervisor 6 times in 6
trials. The cited concerns were real: failing new regression tests, a
contradictory test, and missing delivery fields. The supervisor marked five of
them `correct`. In each case it wrote that it was directing the implementor and
recorded a `wait --disposition correct --note ...`. No steering message was
sent in any trial. A disposition only informs Jev's later classification; it
never reaches the child. Jev's attention therefore changed nothing in this run.
This is a Cerebro protocol defect: the natural action reads as a correction but
is not one.

## Conclusion

The hypothesis is not supported here. Cerebro with Jev produced Terra-quality
code from Luna, but slower and costlier than Terra alone, and it delivered less
often. Two defects are now concrete:

- correction reviews keep finding new marginal issues, so loops do not converge;
- Jev's wake path loses the supervisor's correction, because acknowledging
  `correct` does not steer.

Neither the full four-arm confirmation nor a claim of advantage is justified
until both are fixed and this focused comparison is rerun.
