---
name: cerebro-child-flow
description: Answer a blocked child and recover durable or interrupted work without duplicating it.
---
# Questions and recovery

Children run non-interactively. A genuine unresolved product decision ends
that turn with a question. Read the closing-message banner and native child
ID; incomplete work is not delivery. Answer from the current spec, task or
`recall` when possible. Otherwise relay the exact decision to the user. Use
`answer <child-id> <answer>` to resume the same conversation and assess its
next terminal handoff.

On resume read `status` and `jobs` once. A running detached job survives its parent;
call `wait <job-id>` directly and keep it pending until completion or a scope
notice. Do not poll its logs, progress files or state, or repeat timed waits.
Do not duplicate live work. Read completed final results before continuing.

For an interrupted child with no live job, repeat the same command, repo,
branch and original task-file/prompt. Cerebro uses those keys to resume its
stored native ID. A failed native resume reports an error and preserves that
ID; diagnose it rather than creating a fresh conversation. Authorized restart
is abandonment, with different cleanup consequences; load `cerebro-pair`.
