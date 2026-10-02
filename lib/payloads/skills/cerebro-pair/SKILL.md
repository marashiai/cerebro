---
name: cerebro-pair
description: Handle Jev scope notices and steer native children within task authority.
---
# Live steering

Use `--watch` on execute/apply-review/doc-write for automatic drift detection,
or `--no-watch` to disable the configured default for one call. Jev classifies
small batches of native child events against the current spec, delegated task,
adjustable plan and trusted steering. In-scope progress stays silent. A possible
deviation or uncertainty returns to the parent through the existing command
tool, with original evidence, confidence, worktree, native ID and steering pipe.
Do not consume raw implementation logs or launch another observing agent.

Inspect the evidence and current requirements. Steer a valid correction when
authorized, or request a material user decision. A low-confidence concern is
uncertainty, not proof of drift. A stored notice may predate a scope revision;
dismiss outdated concerns using the current requirements. After handling or dismissing a notice, call
`wait <job-id> --after <sequence>` to acknowledge it and wait for the next
notice or terminal handoff. The child remains available for steering while the
notice is pending. Classifier failure stops the watched child and preserves
its worktree and native conversation for recovery.

`--watch` requires recorded requirements, a configured Jev key and a durable
command-tool or `detach` job. It adds no post-turn delay unless `--pair` is also
selected. `--pair` alone provides manual steering with a bounded idle window.

`steer <pipe> <message>` delivers a native follow-up/turn correction. The
post-turn `CEREBRO_PAIR_IDLE` window is bounded. `[supervisor]`
messages enforce the current spec and delegated task; they never grant new
user authority. Direct `[user]` messages can change requirements, which the
parent records in the spec and adjustable plan. Resolve real product ambiguity.

`restart <pipe> <diagnosis>` applies to execute children, for unrecoverable work with preauthorized or
explicit abandonment and cleanup. It reaps that native child and removes its
isolated worktree, branch and PR. Revise the task from the terminal diagnosis
within the user's requirements and execute afresh; never resume the abandoned
conversation. Use `cancel` for authorized termination without that cleanup.
