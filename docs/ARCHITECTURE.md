# Architecture

Cerebro is a Bash CLI with small sourced modules and stdlib Python controllers.
The entry point resolves its library, sources modules, and dispatches. Each
session records one backend and its native supervisor conversation.

The supervisor plans and adjudicates. The implementor completes the assignment,
including coding and tests. The reviewer independently inspects actual changes
and reports findings. Separate contexts and controller-owned orchestration keep
these responsibilities distinct while preserving native tools and repository
instructions. There is no Cerebro security/tool sandbox.

## Task lifecycle

`execute` accepts one structured task packet. It resolves optional role settings
against session/environment config and preserves native defaults when omitted.
The task's explicit base reference is pinned before checkout selection. Existing
workspace preparation supports the current checkout, an explicitly selected
branch, or an explicitly requested managed worktree without resetting work.

The packet, workspace, stage, current attempt paths, and completed output
references live in `session/tasks/<task-id>/task.json`. Native IDs remain in the
existing locked child store. The packet also materializes a task-local `spec.md`;
`session/spec.md` is informational. Jev reads authority from the canonical packet in that task.json.

The lifecycle is implementation -> review -> assessment -> done. Incomplete,
blocked, question, failed transport, and malformed handoffs preserve the current
stage and original evidence. Answer resumes the questioned stage. Restart retires
the native conversation and preserves its diagnosis/workspace for a fresh attempt.
Complete is a stage outcome, not an acceptance verdict. The supervisor owns that
verdict and any focused correction packet.

A task lock is inherited by the stage runner, so controller interruption cannot
permit a second controller to duplicate still-running native work. Each intended
attempt has durable prompt/log paths before launch. The runner records its exit
receipt after native transport completion. Recovery consumes a matching successful
receipt and raw handoff without rerunning the stage; a failed transport receipt
cannot be mistaken for implementation delivery. Completed output and next stage
are saved in one authoritative transition before native-ID bookkeeping.

## Native transports

Pi uses its RPC acknowledgements and settled events, Codex its app-server
thread/turn API, and Claude its stream-json messages. Existing adapters track
native admission, busy tools, completion ordering, and steering across turn
boundaries. The final parser records conversation identity as soon as durable.
All stages expose a steering FIFO and retain event logs, prompts, raw closing
replies, errors, and exit receipts. Normal quiet tools have no automatic stall
retry or quiet-time kill. An explicit wall-clock timeout remains configurable.

Native supervisor launches retain user tools/configuration and add the small
orchestration MCP server. Codex exposes its namespace directly in CodeMode;
Pi exposes the command directly through its native MCP extension. Implementors
and reviewers receive no supervisor orchestration server or mandatory skill
injection. Repository development skills remain source files for AGENTS.md.

Original user inputs are captured before inference or delegation in the ordered
session `user-inputs.json`, independently of the supervisor's task packet.
Claude and Codex use synchronous `UserPromptSubmit` hooks; Pi awaits its input
extension, binding native identity before a transcript file exists. Codex obtains
its hook key/hash through native `hooks/list` and trusts that exact definition
only for the launch, preserving existing user hooks and trust configuration.
ACP captures structured prompt blocks before forwarding. Programmatic callers
capture explicitly and set `CEREBRO_INPUT_OWNER=external` to suppress duplicate
native capture. Each submission retains exact text and receives its own identity;
there is no transcript backfill or replacement with the model-authored plan.

## Durable event delivery and Jev

The command MCP launches long tasks through existing detached monitors, which own
completion sockets and survive parent disconnects. A waiting command returns on
a concern or completion. `wait --after` acknowledges a handled notice and blocks
for the next event. Cancellation owns the selected monitor's descendants only.

Jev consumes native event batches, the task-local goal, and supervisor decisions.
A concern contains evidence references, and the supervisor decides how to act.
`wait --disposition --note` appends `{job_id, sequence, disposition, reason, ts}`
to session `decisions.jsonl`. The job ID is passed by the detached monitor through
`CEREBRO_JOB_ID`; `CEREBRO_TASK_FILE` identifies the canonical packet, and
`CEREBRO_JOB_STATUS` identifies its notification socket.

Review output is strict JSON with per-finding anchors and criterion evidence.
Jev preserves that original report and writes a sibling assessment/HTTP trace.
Assessment retry has its own stage so failure does not cause another native review.
The final task result returns original implementation, original review, and
assessment inline for supervisor adjudication.
