# Using Cerebro

Start `cerebro` in a terminal, describe the goal and repository, and let the
supervisor inspect and plan. Use `cerebro --resume [session-id]` to reopen the
recorded native supervisor conversation. `cerebro list` lists sessions.

The supervisor keeps its normal native inspection tools. Its Cerebro MCP tool
provides only task orchestration: execute, answer, steer, restart, wait, cancel,
status, jobs, and worktrees. Implementors and reviewers keep normal native tools
and receive task-local instructions, without the supervisor orchestration MCP.

## One task packet

The supervisor calls the command tool directly with `argv: ["execute"]` and a JSON
packet in `stdin`. A shell can provide the same packet on stdin, or use
`cerebro execute --packet /absolute/task.json` within an active Cerebro session.

```json
{
  "goal": "Improve login error handling",
  "task": "Inspect the existing flow, implement precise error messages, and test the success and failure paths. Leave changes uncommitted.",
  "acceptance": [
    "Invalid credentials show the documented error without exposing account existence",
    "Successful login behavior is preserved",
    "The existing login tests pass"
  ],
  "repo": "/absolute/path/to/repository",
  "base": "main"
}
```

Required fields are goal, task, acceptance (a nonempty array), repo (an absolute
Git checkout path), and base (an explicit Git reference). The base is resolved
before any checkout selection and pinned for the independent review. Existing
uncommitted work is preserved. Optional `branch` selects or creates a branch;
`worktree: true` requests an isolated managed checkout. Existing branches retain
their own history; base seeds only a new branch/worktree.

Optional role overrides have this shape:

```json
{
  "models": {
    "implementor": {"model": "your-native-model", "effort": "your-native-effort"},
    "reviewer": {"model": "your-other-native-model", "effort": "your-native-effort"}
  }
}
```

Each setting resolves from packet override, environment/config role default,
then the native backend default. Explicit empty strings request native defaults.
Model IDs and effort strings are passed unchanged. Cerebro does not prescribe a
provider's model catalogue or effort vocabulary. Use a different configured
reviewer model when available; same-model choices remain valid and visible.

The controller runs coding and tests first, then an independent reviewer with
the original goal, plan, acceptance, pinned review base, and test evidence. It
returns implementation evidence and the original review inline. A focused
correction uses a new packet with `correction_of` set to the reviewed task ID and
the same repo and base, without `branch` or `worktree`. It reuses that task's
checkout on the same branch. Its review covers the accepted findings, every change
since the earlier review (including effects on code that depends on it) and scope
since the base. Resubmitting an identical resolved packet returns
its durable result rather than repeating completed work.

## Handoffs and recovery

Children return a final JSON object with `status`, `summary`, and criterion
results. `complete` means the stage finished, never that every acceptance
criterion passed. Criterion results are passed, failed, or unverified, with
concrete evidence. Implementation additionally supplies test evidence; review
supplies individually anchored findings. Original messages and native event
logs remain available even when a handoff is malformed.

A stage ends when its native turn ends, even if a tool call it started never
reported completion. Those calls are retained in an `.unfinished.json` receipt
beside the child log and returned as `implementation_unfinished_tools` or
`review_unfinished_tools`; their results never reached the stage's report.

Implementation statuses question, blocked, unfinished, and failed stop before
review. A question includes its requested decision. Continue it with:

```bash
cerebro answer <task-id> "The answer"
cerebro execute --resume <task-id>
```

`answer` is for the current question stage; it also works for reviewer questions.
`--resume` retries an interrupted/failed stage in its recorded native conversation.
Completed stages are retained. If no native conversation exists for a failed
stage, start a corrected packet rather than silently recreating the work.
Restart explicitly retires a conversation, preserves checkout and diagnosis,
and permits a fresh conversation on task resume.

`cerebro status` summarizes task IDs, stages, and workspaces. `cerebro jobs` finds
persistent jobs, including jobs whose parent disconnected. `cerebro recall text`
searches retained session logs.

## Jev concerns and steering

Configure Jev with `jev_enabled: 1` and `jev_api_key` (or corresponding
`CEREBRO_JEV_*` environment variables). The observer watches native events using
the canonical task packet’s goal and criteria. Concern notices wake a pending orchestration
call; healthy execution otherwise remains quiet. A notice requires a cited event,
a concrete reason, and confidence at or above `jev_confidence`; each event and
reason wakes the supervisor once. Every classification stays in the child's
`.scope.jsonl` log. Observer failures remain explicit.

Answer a concern with a disposition:

```bash
cerebro wait <job-id> --after 1 --disposition continue --note "The cited work is required by acceptance criterion 2."
cerebro wait <job-id> --after 2 --disposition correct --note "Fix the failing regression test before continuing."
```

Disposition is continue, correct, or stop. For `correct`, the note is delivered to
the cited implementor as a steering message before the decision is recorded; the
implementor reads it after its current tool call. Add `--interrupt` to stop the
running turn and its tool calls first, so the note starts the next turn at once;
use it only when the running call is wasted or harmful, and write the note as the
complete next instruction, because the stopped turn is over and the interrupt
supersedes earlier steering the implementor has not yet read. If the implementor has
finished, `correct` fails and records nothing. For `continue` and `stop`, the note is a reason
and never reaches the implementor. Every decision is recorded in the session's
`decisions.jsonl`, scoped to job ID and notice sequence, for Jev's next assessment.
The wait blocks until the next notice or completion. It does not poll status.
Steer, restart or cancel directly at any time:

```bash
cerebro steer [<pipe>] "Use the existing login helper and rerun its tests"
cerebro steer --interrupt [<pipe>] "Stop this full-suite run; test only the parser module"
cerebro restart [<pipe>] "Inspect retained changes and replace the incorrect approach"
cerebro cancel <job-id>
```

Steering is available during normal execution with Jev disabled too. A steer
reaches the running turn at the implementor's next model step, after its current
tool call. Cancellation
stops only that job's descendants. A restart retains work; checkout cleanup is a
separate `cerebro worktrees cleanup` action that preserves work still in use.

## Native configuration

`CEREBRO_BACKEND` chooses Pi, Codex, or Claude; an existing session restores its
recorded backend for all roles. The role model keys are `supervisor_model`,
`model` (implementor), and `review_model`. Effort keys are `supervisor_effort`,
`implementor_effort`, and `review_effort`. The corresponding environment variables
are uppercase with `CEREBRO_` prefix. No model or effort is hardcoded.

Authentication, native extensions/MCP configuration, native repository context,
and normal coding/command tools remain available. Cerebro owns only the
supervisor orchestration server and native session binding. Custom Claude
endpoints use `claude_base_url` and `claude_auth_token`; role model overrides are
restored to the original native model environment when a role uses native defaults.

`cerebro models` shows the existing optional native model catalogue;
`cerebro model-env <id>` prints declared Claude context settings. Cerebro does not
install models or change provider authentication.

`cerebro acp` supplies the Claude editor frontend. Pi and Codex use native terminal
sessions. `cerebro cerebro-mcp` provides the generic event-driven PTY frontend for
controllers that need to operate an interactive native session.
