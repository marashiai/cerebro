# Original context retained; delivery still stalled

The unchanged lease-queue case was rerun at `107c19e` after Cerebro began
capturing original user inputs independently of the supervisor's task packet.
The case, grader, models, efforts, ordering seed and deadline match the
[earlier challenge](../2026-10-04-lease-queue/interpretation.md). Bare Terra used
medium effort; Cerebro used Luna low for implementation and Terra medium for
supervision and review. Each arm had one 300-second parent deadline, including
all child work, review and corrections.

| Condition | Delivery | Recorded seconds | Saved code after shutdown | Recorded input + output tokens | Estimated USD |
| --- | --- | ---: | --- | ---: | ---: |
| Bare Terra | Passed | 126.1 | 19/19 behavioral checks; scope preserved | 164,559 | 0.1457 |
| Cerebro without Jev | Timed out | 301.5 | 19/19 behavioral checks; scope preserved | 319,476, partial | Unknown |
| Cerebro with Jev | Timed out | 301.5 | 19/19 behavioral checks; scope preserved | 333,819, partial | Unknown |

[Report and charts](report.md) retain both timeout failures. Elapsed time includes
setup and cleanup. Interrupted parent usage is incomplete, so total supervised
prices remain unknown. Prices use the recorded API reference rates and are not
account bills.

After all trials stopped, the same frozen grader was run on every saved checkout.
[Supplemental checks](supplemental-functional-checks.json) record the results and
unchanged source fingerprints. These checks establish saved-code behavior; they
do not change the original delivery outcomes.

## What the context fix changed

The complete original request, including required `None` and `False` returns,
appears verbatim in both implementation prompts and both review prompts. All six
Jev requests also contain the captured original input. The previous return-value
defect did not recur in this run. Regression tests separately verify that an
omission from the supervisor's packet does not remove original requirements
from the downstream context.

Codex's synchronous `UserPromptSubmit` hook was verified against the installed
native app-server: capture was durable before its first local test-provider
request. The programmatic eval records the original input directly before
inference. This fixes a Cerebro integration gap; Codex supports the required
hook mechanism.

## Why both supervised deliveries timed out

Both reviewers launched a Python multiprocessing check through standard input.
On this macOS host, the default spawn method failed to import the `<stdin>`
program. The check then waited indefinitely for missing queue results. Each
reviewer ran a replacement check using an explicit fork context but left the
first command running before returning final review JSON.

The native turn finished, while Cerebro's transport still had one outstanding
command. Its completion predicate correctly refused to call that command
finished, but it did not surface an unfinished-check result to the supervisor.
The controller stayed at `review/running`, without materializing the review
handoff. The supervisor never received or adjudicated either review.

Without Jev, implementation took about 93 seconds and review took 105 seconds;
the final native review turn ended roughly 226 seconds into the parent run.
The remaining 74 seconds were spent waiting on the unfinished command. With
Jev, implementation took about 47 seconds and review took 119 seconds; the
review turn ended around 201 seconds, leaving about 99 seconds blocked.

The no-Jev reviewer reported token reuse under a patched constant random-token
generator. The Jev-arm reviewer reported no code findings and left a delivery
metadata criterion unverified. Neither report reached the supervisor, so this
run does not measure the quality of its review adjudication.

## What Jev contributed

Jev completed six implementation assessments: three quiet and three uncertain.
The uncertain notices came from low-confidence possible-issue classifications
(0.20, 0.33 and 0.53, below the configured 0.8 threshold), all with reason `none`.
Two notices cited the same ordinary file-inspection command; the third cited
the implementor's closing metadata. The supervisor acknowledged all three with
`continue`, and no native steering was accepted. These notices established no
concrete repository defect or demonstrated correction.

All six requests succeeded; there were no observer/provider failures. Recorded
Jev cost was approximately $0.0022. Review assessment never ran because the
review stage did not finish, so the configured Jev review feature was not
exercised in this trial.

## Implication

This remains evidence against a delivery advantage on this selected task.
Preserving source requirements fixed a demonstrated information-loss mechanism,
but it did not make Cerebro finish the workflow. One run per condition cannot
establish a general quality or efficiency improvement.

The next transport change should surface a terminal turn with unfinished checks
to the supervisor immediately, including the original report and outstanding
command evidence. Silently clearing the busy state would hide incomplete work.
Jev should also avoid waking the supervisor for repeated, low-confidence
observations that identify no concrete concern. Both issues can be tested
directly before choosing another model-quality challenge.
