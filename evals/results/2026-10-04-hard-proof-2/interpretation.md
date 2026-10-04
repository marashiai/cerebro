# Cerebro with Jev against bare agents, after the steering and review fixes

This rerun repeats the [first hard-task proof](../2026-10-04-hard-proof/interpretation.md)
at `aaecbb0`, with the same frozen tasks, graders, models, efforts and 600-second
deadline. Since that run, Cerebro has gained four changes:

- scoped correction reviews;
- `correct` dispositions that reach the implementor;
- mid-turn steering through each backend's native protocol;
- review findings labelled as stated-requirement violations or robustness concerns.

| Arm | Inventory | Patch apply | Delivered | Mean seconds | Mean estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bare Luna | 0/3 | 0/3 | 0/6 | 73 | 0.004 |
| Bare Terra | 1/3 | 1/3 | 2/6 | 171 | 0.177 |
| Cerebro with Jev | 3/3 | 1/3 | 4/6 | 373 | 0.341 |

[Report and charts](report.md) keep every failure, and all six trials per arm
have complete usage records. The 95% intervals overlap widely (Terra 10–70%,
Cerebro 30–90%), so this is descriptive evidence, not a significant difference.

## Where supervision helped

On inventory reservations, all three of Luna's first implementations under
Cerebro broke replay of committed reservations. Each reviewer reported this as
a high-severity requirement finding and quoted the replay rule. The supervisor
ran one focused correction, and its scoped review found nothing further. All
three were delivered in 220–288 seconds. Bare Luna failed all three trials, and
bare Terra failed two, each time missing the validation-precedence rule.

This is the intended mechanism: a fast implementor makes a real mistake,
independent review catches it from the stated requirements, and one narrow
correction fixes it.

## Where it did not

On patch apply, two of three Cerebro trials used both permitted corrections:

- Trial 1 delivered correctly at 558 seconds.
- Trial 2 delivered code that failed CRLF, no-newline, reverse and generated-case
  checks.
- Trial 3 delivered after one correction but missed the offset carried into the
  next hunk.

The new finding labels did not stop the loop. Reviewers labelled every finding
`requirement`, including two kinds that are not stated requirements:

- extrapolations, such as rejecting "impossible" no-newline markers, from the
  rule that the marker "applies to the preceding line";
- quotations of the supervisor's own correction packet, such as "Add focused
  forward and reverse regression coverage", cited as if they were the user's
  requirements.

## Jev

Jev woke the supervisor once in six trials (`skipped_verification`, 0.86). The
supervisor chose `continue`, so no steering was sent. The delivery gain in this
run comes from independent review and correction, not from Jev.

## Compared with the first proof

Bare Luna and bare Terra ran with identical settings in both proofs:

- Bare Terra delivered 5/6 then 2/6.
- Cerebro with Jev delivered 3/6 then 4/6, with different Cerebro code between
  the runs.
- Over the 12 Terra trials, Terra delivered 7/12, the same count as Cerebro.

The run-to-run variance is as large as the difference between arms. The
supported conclusion is narrower than "Cerebro beats bare agents": supervision
consistently lifts Luna (2/12 bare against 7/12 supervised) and is competitive
with Terra on delivery, at about 2.2 times Terra's time and 1.9 times its
estimated cost in this run. That is well outside a 50% overhead target.

## Next

- Accept a `requirement` finding only when its quote appears in the original
  user inputs or acceptance criteria. This rejects quotations of the
  supervisor's own correction text mechanically. The supervisor still judges
  extrapolations from real rules.
- Overhead comes from planning (about 25 s), Terra review (50–135 s) and any
  correction cycle. Reaching 1.5 times bare Terra needs a single-pass delivery
  and a faster review; effort settings would be a separately labelled
  experiment.
- Jev's value is still undemonstrated. It needs a task where the implementor
  drifts mid-run, so a wake can change the outcome.
