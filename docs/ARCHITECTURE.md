# Cerebro architecture

Cerebro is a Bash command harness around three separate native backends:
OpenCode V2, Codex, and Claude Code. A session selects one backend for its
supervisor, implementers and reviewers. There is no communication or handoff
between backends. See [USAGE.md](USAGE.md) for workflows and [AGENTS.md](../AGENTS.md)
for source conventions.

## Roles and commands

The parent is a supervisor: it owns requirements, plans, delegation and delivery
gates. Development, tests, runtime verification and documentation happen in
children. A separate observer reads live activity and compares it with the
approved spec and plan; preauthorized steering corrects drift without creating
new requirements.

```text
user → native supervisor → Cerebro command(argv, stdin)
                            ├─ guarded inspection and session records
                            └─ detached task → native child → final handoff
         native observer → observe / authorized steer or restart
```

The `cerebro` MCP server exposes one `command` tool. Arguments are literal
strings, not shell text; plan/spec bodies travel in `stdin`. Role allow-lists
restrict commands: the supervisor delegates work, the observer reads and steers,
and the reviewer gets only guarded file and git inspection. The server invokes
the same Bash CLI used from a terminal. It does not implement another planner,
agent scheduler or model loop.

Native restrictions prevent the parent and reviewers from using unrestricted
mutation tools. The command server runs outside the native parent's sandbox so
it can launch authorized writable children; shell subprocesses inherited from a
read-only sandbox could not provide that boundary. Read-only bridges validate
verbs, flags and paths and execute argv directly. They reject unsafe git/gh
operations and path escapes, and propagate useful diagnostics.

## Shared skills, small backend adapters

`lib/payloads/skills/` is the source of the supervisor, observer, worker and
workflow instructions. Launch materializes these into `$CEREBRO_HOME/.agents/skills/`;
Claude's `.claude/skills/` entries point at those same files. `guide <skill-name>`
loads a skill through the command tool, so a backend need not expose a native
skill loader to use the shared workflow. Specialized guidance loads when needed
rather than living in a large always-loaded prompt.

OpenCode uses its built-in agents with shared skill instructions. Cerebro does
not create native OpenCode agent definitions. Known Cerebro V1 agent files are
removed when the home is materialized; there is no V1 execution path.

| Backend | Parent | Children and steering | Read-only enforcement |
|---|---|---|---|
| OpenCode V2 (minimum 2.0.19) | Native standalone TUI | Local `opencode serve`, public session APIs and event stream | Plugin tool restriction plus session permissions; Cerebro MCP remains directly callable |
| Codex | Native TUI and native resume | Stdio `codex app-server`, native threads/turns and atomic start-or-steer through `turn/start` | Native read-only sandbox; unrelated host tools and MCP servers disabled for guarded roles |
| Claude Code | Native TUI and native resume | Native `claude -p` stream-json, additional input turns when paired | Native tool removal and strict Cerebro-only MCP configuration for guarded roles |

Writable children retain the native backend's development tools and configured
model access. `verify` needs runtime/browser capability; audit, review and
improvement analysis are read-only roles. A review model may differ, but always
belongs to the session's backend. Empty model settings leave model selection to
the native backend. Authentication remains native to each CLI.

The OpenCode plugin must be active before guarded work starts. Its prompt hook
pins session permissions after agent permissions, preventing a permissive user
agent configuration from restoring mutation access. Codex explicitly selects its
read-only sandbox on launch/resume and restricts host-side capabilities. Claude
removes native tools rather than treating an auto-approval list as confinement.
Backend setup failures stop the command instead of silently weakening a role.

## Native completion and durable jobs

The supervisor's command tool automatically detaches long child commands. A
monitor in a separate process session owns the task and stores its job, PID,
output and final status under the Cerebro session. Disconnecting or timing out
the parent's tool call does not terminate the task. `status` and `jobs` let a
resumed supervisor find existing work instead of launching a duplicate.

`wait <job-id>` blocks on a Unix socket completion notification. The monitor
publishes the final status before notifying waiters, so waiting after completion
also works. There is no loop polling the child's PID, status file or logs to
schedule the supervisor. Missing completion transport while a job still claims
to be running is reported as a failure.

Backend adapters consume native events or stdout directly. OpenCode's agent
turn can finish while a native background shell is still running, so the adapter
joins its shell completion notification and following agent turn. Codex likewise
joins outstanding command items before handing back a completed turn. Workers
must join all finite background work and clean up servers they started before
their final handoff. Arbitrary detached OS processes are not a native completion
contract. A configured wall-clock limit bounds child work; paired children also
have inactivity bounds and a short post-turn steering window.

Pair mode adds a FIFO for steering; native backend input APIs deliver the next
instruction. Restart is destructive within the task's isolated worktree,
branch and PR, and requires explicit or prior authorization. Cancellation stops
the detached monitor and its descendants. Neither action changes the approved
requirements on its own.

## Durable state and identity

Cerebro owns a session ID independently of each backend's conversation ID.
Metadata records the backend, role and native conversation binding. OpenCode
binds through its plugin, Claude through its user-prompt hook, and Codex through
its native turn-completion notification. Child IDs are captured when native
creation events arrive, enabling same-conversation resume after an interruption.
An invalid resume fails; it does not silently start fresh work.

```text
$CEREBRO_HOME/
  .agents/skills/                  shared role and workflow skills
  .claude/skills/                  links to the shared skills
  templates/                      user-editable repository bootstrap instructions
  learnings.md, pending-learnings.md
  overlays/                       user-owned prompt additions
  worktrees/<task-key>/            isolated implementation worktrees
  sessions/<id>/
    metadata.json, transcript.jsonl
    spec.md, spec-history.jsonl
    plans/                        technical plans and readable companions
    audits/, children/            findings, native logs and steering records
    child-sessions.json           native child IDs and lifecycle status
    detached-jobs/                persistent ownership and completion records
    review-state/                 per-repo/branch review checkpoints
```

The spec is the requirements of record. Replacing it archives the prior version.
Plans may adapt within the approved contract; changes to requirements need user
authority. Technical plans remain executable truth, with readable companions
updated alongside them. Follow-up review, fixes and documentation use the
worktree announced by `execute`, not the user's main checkout.

Only incomplete children auto-resume, within the configured retention period.
Cleanly completed work gets a fresh child next time. A blocked child ends with a
question; `answer <child-id> <answer>` resumes that exact conversation. The
supervisor answers from the recorded contract or relays a real product decision.

## Observation and delivery gates

Observation intentionally consumes log batches; it is separate from waiting for
child completion. A per-observer cursor avoids rereading delivered activity. The
supervisor receives concise handoffs and does not consume implementation logs.
Steering records preserve `[observer]`, `[supervisor]` or direct `[user]` source.
Observer/supervisor corrections may enforce the approved scope, never masquerade
as user requirements. A cheap observer classifier using jev and typesafeai is
planned separately and is not implemented here.

The shared workflows preserve plan approval, high-risk plan audit, isolated
worktrees, independent review, bounded correction and real end-to-end
verification. Each step in a suite must independently build, pass tests and keep
the app workable. Static review cannot certify runtime criteria; a blocked
verification requires actual confirmation before delivery. Commit, push, PR,
infrastructure and destructive actions remain governed by user authorization and
repository instructions. Cerebro does not serialize competing mutations against
the same repository; the supervisor must sequence them.

## Frontends and source boundaries

Terminal sessions support all three backends. ACP is a thin editor proxy for
OpenCode and Claude; it preserves native protocol capabilities, binds native
session IDs and pins the guarded supervisor role. OpenCode uses its built-in
mode; Claude ACP uses a small native wrapper around the shared supervisor skill.
Codex has no native ACP endpoint and is supported through its terminal interface.
The optional PTY MCP frontend drives real terminal sessions and provides its own
completion notifications; it is distinct from the guarded command MCP server.

`bin/cerebro` resolves the library and dispatches. `lib/backend.sh` selects the
native adapter; `lib/commands/` owns workflows and read-only bridges; shared
prompts/config live in `lib/payloads/`; Python helpers own protocol parsing,
process lifecycle and native transport. No backend-specific workflow is
required. Run `bash tests/run.sh` before delivery; fixtures supplement native
runtime verification rather than replacing it.
