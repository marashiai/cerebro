## Analyzer -- diagnosis policy

What to look for, grounded in the traces:

* Repeated child failures or stalls (execute / apply-review / doc-write):
  the same wrong turn, missing instruction, or misread role constraint
  showing up across multiple sessions.
* Grader noise: the audit/review grader repeatedly flagging the wrong
  thing, missing a class of real problem, or producing an unusable verdict.
* Orchestrator mis-steps: the same planning/looping/escalation mistake
  recurring across sessions, or a preference the user had to repeat.
* Prompt/tool-surface gaps: a child lacking an instruction or an allowed
  tool it clearly needed, visible as repeated retries or dead ends.

For each diagnosed pattern, assign a short failure tag (at most 15 words)
that names the failure class specifically enough to distinguish it from
unrelated patterns but broadly enough to match related instances.
Distinguish between skill-addressable failures (a prompt or instruction gap
the harness can fix) and base-model capability limits (the model simply
cannot do it -- not harness-addressable, do not file).
