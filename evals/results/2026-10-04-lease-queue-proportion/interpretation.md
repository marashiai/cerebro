# Supervisor judgment, three repetitions

This run repeated the unchanged lease-queue case three times at `14d18d4`. That
commit makes the supervisor explicitly responsible for deciding which review
findings merit correction: it should judge each finding by how likely and how
harmful its failure is, and remember that a correction costs another
implementation and review. Models, efforts, ordering seed and the 300-second
parent deadline match the [previous run](../2026-10-04-lease-queue-unfinished/interpretation.md).
Trials ran one at a time.

| Arm | Delivered | Mean seconds | Estimated USD | Saved code after shutdown |
| --- | ---: | ---: | --- | --- |
| Bare Terra | 3/3 | 175.1 | 0.1495 per task, complete | 3/3 pass 19/19 checks, scope preserved |
| Cerebro without Jev | 2/3 | 215.1 | unknown (one interrupted trial) | 3/3 pass 19/19 checks, scope preserved |
| Cerebro with Jev | 1/3 | 277.6 | unknown (two interrupted trials) | 3/3 pass 19/19 checks, scope preserved |

[Report and charts](report.md) keep every failure in its denominator. Prices are
API reference estimates; missing usage is unknown, never zero. The
[supplemental checks](supplemental-functional-checks.json) rerun the frozen
grader on all nine saved checkouts after the run. They describe saved code, not
delivery.

## Per trial

| Trial | Arm | Outcome | What happened |
| --- | --- | --- | --- |
| 1 | Bare | Pass, 219 s | |
| 1 | No Jev | Timeout | Review ended at 219 s with a medium finding (non-string identifiers are coerced by JSON) and a low one (rejected calls rewrite the file). The supervisor accepted both; the correction did not finish. |
| 1 | Jev | Pass, 290 s; condition invalid | Review raised a high finding: a finite `now` plus `lease_seconds` can overflow to infinity. The correction worked, but its closing handoff was rejected twice because it restated one criterion with `/` for `,`. Its review never ran, so the delivered code was not independently reviewed. |
| 2 | Bare | Pass, 143 s | |
| 2 | No Jev | Pass, 188 s | No findings. |
| 2 | Jev | Correct code; condition invalid | Review raised a low finding: `claim` with an unknown identifier and an invalid lease raised `ValueError`, not `KeyError`. The supervisor reproduced it, and the correction and its second review finished at 239 s. The trial fails because of the Jev provider error described below. |
| 3 | Bare | Pass, 161 s | |
| 3 | No Jev | Pass, 154 s | No findings. |
| 3 | Jev | Timeout | Review took 128 s and raised a low finding: failed calls and reads rewrite the file. The supervisor accepted it; the correction review was interrupted. |

## Supervisor judgment

The guidance did not make the supervisor decline findings. Every supervisor that
received a finding gave an explicit reason for correcting it. Each described the
finding as a concrete violation of the stated contract: "without mutation",
"unknown identifiers raise `KeyError`", or a finite input domain. One reproduced
the claim before delegating. Each requested a narrow correction. On the task text
these decisions are defensible, and none was a hallucinated defect.

Their cost decided the outcome. With these models, the first implementation and
review finished 132–219 seconds into the run, so any correction leaves too
little of a 300-second budget. Neither the bare agent nor the supervisor is told the deadline, and
telling only Cerebro would be unfair. Under this budget, any accepted finding
makes a timeout likely, whatever its merit.

A supplemental probe, not part of the frozen grader, shows the findings were
real for the code they reviewed, and that other arms share them:

- All three bare implementations raise `ValueError` for an unknown identifier
  with an invalid lease. Only the Jev trial 2 correction raises `KeyError`.
- Bare trial 2 accepts a lease whose expiry overflows to infinity.
- Two passing supervised implementations rewrite the file on rejected calls
  (no Jev trial 2 and Jev trial 1); their reviewers did not flag it.

Review therefore catches contract edges that bare Terra leaves, but
inconsistently, and the frozen grader does not reward those edges. In this case
supervision bought those fixes with delivery failures.

## Lifecycle defects found

The handoff rejection in Jev trial 1 was a Cerebro defect. The controller
required each criterion's text to be repeated exactly, and on resume it sent the
original prompt without saying why the handoff had been rejected. The worker
therefore repeated the same harmless paraphrase. Commit `70c283a`, made after
this run, matches criterion results by position and tells a resumed stage why
its closing JSON was rejected.

The Jev failure in trial 2 was a provider contract inconsistency. Jev chose
`possible_issue` with probability 0.47 while `quiet` had 0.48. Rounding cannot
explain that order. Cerebro rejected the response and reported an observer
failure to the supervisor, as designed. The supervisor recorded it and continued.
Validation is unchanged; this should be reported to the Jev provider.

Jev itself made 12 to 24 implementation classifications per trial and published
no concern notice; trial 2's only notice was the observer failure. Its review assessments rated all three findings
useful and proportionate, with validity uncertain. They did not change any
supervisor decision.

## Implication

Across four supervised trials with findings, supervision finished within the
deadline twice and timed out twice. Bare Terra delivered every time and was faster. The
supervisor's judgment was reasonable on the contract, but the workflow's fixed
cost per correction is too high for this budget. The open design question is
who should accept known residual contract edges, and how. Giving Cerebro a
larger budget would favor it unfairly and would not change the cost shown
above.
