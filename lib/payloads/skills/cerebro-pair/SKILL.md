---
name: cerebro-pair
description: Run observable children and handle live steering or authorized restart without redefining scope.
---
Pass `--pair` to `execute`, `apply-review`, or `doc-write` when live observation
or steering is needed. All backends expose the same FIFO side channel. Native
streams report completion; there is no supervisor log or child-state polling.
The command tool owns a persistent detached job and waits for its completion.
Relay the Cerebro session ID so a separate observer can watch it. Do not consume
raw implementation logs in the supervisor.

An observer runs `observe <session-id>`, reads the approved spec and plan, and
compares each batch against them. Preauthorized autosteering may correct drift,
unsupported assumptions or skipped verification. Otherwise steering requires
the user's instruction.

Use `steer <pipe> <message>` for a bounded correction. OpenCode and Codex deliver
it through native session/turn APIs; Claude accepts its next stream-json turn.
After a terminal turn, `CEREBRO_PAIR_IDLE` gives a short steering window.
Completed work does not remain open indefinitely waiting for a person.

Every steering record names its source. `[observer]` and `[supervisor]` are
corrections within the approved contract, never user authority. Do not change
the spec or broaden the plan because of them. `[user]` comes from a direct
external user steering command; fold an unambiguous requirement change into
the spec and affected plans, then report what changed. Ask about a real product
ambiguity instead of guessing. Direct instructions already in the parent chat
retain their user authority independently of these records.

Use `restart <pipe> <diagnosis>` only when abandonment and cleanup are
preauthorized or explicitly requested, and a nudge cannot recover the child.
Cerebro reaps the native child and cleans that task's isolated worktree,
branch and PR. Its terminal restart block gives the diagnosis. Correct the
plan within the approved spec, regenerate its readable companion, and re-run
fresh on the new announced worktree. Do not resume the abandoned native child
or silently reinterpret the diagnosis as a new requirement.
