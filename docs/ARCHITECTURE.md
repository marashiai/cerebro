# Cerebro architecture

Cerebro is a Bash command harness around three separate native backends:
Pi, Codex, and Claude Code. A session selects one backend for its
supervisor, implementers and reviewers. There is no communication or handoff
between backends. See [USAGE.md](USAGE.md) for workflows and [AGENTS.md](../AGENTS.md)
for source conventions.

## Roles and commands

The parent is a supervisor: it owns requirements, plans, delegation and delivery
decisions. Development, tests, runtime verification and documentation happen in
children. Ordinary work uses a short, adjustable plan of possible commits.
Optional Jev watching batches native child events into cheap scope
classifications and wakes the waiting supervisor with cited evidence. The
supervisor retains the steering decision and delivery authority.

```text
user → native supervisor → Cerebro command(argv, stdin)
                            ├─ guarded inspection and session records
                            └─ detached task → native child → final handoff
         native events → Jev Choice → scope notice → waiting supervisor
```

The `cerebro` MCP server exposes one `command` tool. Arguments are literal
strings, not shell text; plan/spec bodies travel in `stdin`. Role allow-lists
restrict commands: the supervisor delegates work and responds to scope notices;
the reviewer gets guarded file/git inspection plus additive Hunk notes. The
server invokes the same Bash CLI used from a terminal. It does not implement
another planner, agent scheduler or model loop.

Native restrictions prevent the parent and reviewers from using unrestricted
mutation tools. The command server runs outside the native parent's sandbox so
it can launch authorized writable children; shell subprocesses inherited from a
read-only sandbox could not provide that boundary. Read-only bridges validate
verbs, flags and paths and execute argv directly. They reject unsafe git/gh
operations and path escapes, and propagate useful diagnostics.

## Shared skills, small backend adapters

Cerebro keeps minimal supervisor, worker and lifecycle instructions in
`lib/payloads/skills/`. The shared `cerebro-worker` skill describes writable child
roles; the upstream `engineering` skill supplies implementation and verification
practice. `supervise` provides the explicitly selected unattended workflow and
`hashimoto-review` supplies explanatory review. Cerebro's former audit-gate and
suite skills are replaced by these upstream workflows.

The three upstream skills are vendored from
[marashiai/skills](https://github.com/marashiai/skills/tree/ef83e8e1d3a914de5ee79ff99521bf90dc0cd4f3),
pinned to `ef83e8e1d3a914de5ee79ff99521bf90dc0cd4f3`. They are reviewed source
inputs, not downloaded dynamically at launch. Launch materializes shared skills
into `$CEREBRO_HOME/.agents/skills/`; Claude's `.claude/skills/` entries point at
those same files. `guide <skill-name>` loads them through the command tool.
Specialized guidance loads when needed rather than living in a large
always-loaded prompt. Native backends execute it independently.

Pi uses its native terminal and RPC interfaces, shared skills and one small
managed extension. The extension reuses Pi's native MCP support with only the
Cerebro server through `createMcpExtension`, binds session identity and enforces
the guarded role. It does not install third-party MCP, subagent or permission
extensions. Native provider
authentication remains Pi's responsibility.

| Backend | Parent | Children and steering | Read-only enforcement |
|---|---|---|---|
| Pi (minimum 0.99.2) | Native TUI and native session resume | Native `pi --mode rpc`, tool/message events and steering commands | Managed role extension, tool gates and isolated resources; only the Cerebro MCP server for guarded roles |
| Codex | Native TUI and native resume | Stdio `codex app-server`, native threads/turns and atomic start-or-steer through `turn/start` | Native read-only sandbox; unrelated host tools and MCP servers disabled for guarded roles |
| Claude Code | Native TUI and native resume | Native `claude -p` stream-json, additional input turns when paired | Native tool removal and strict Cerebro-only MCP configuration for guarded roles |

Restricted Codex roles disable the code-mode host and shell executors. The
`mcp__cerebro` namespace is configured for direct calls so code-mode models can
still use the guarded tool. Catalog-advertised executor calls are rejected at
dispatch; the read-only sandbox alone would still permit native shell commands.

Claude's owned MCP server uses `alwaysLoad: true` to exempt its command tool from
deferred discovery. Guarded roles expose only that command tool; native mutation
and delegation tools stay unavailable.
See Claude's [MCP startup behavior](https://code.claude.com/docs/en/mcp#exempt-a-server-from-deferral).

Writable children retain the native backend's development tools, unrestricted
shell command execution and configured model access. `verify` needs
runtime/browser capability; audit, review and
improvement analysis are read-only roles. A review model may differ, but always
belongs to the session's backend. Empty model settings leave model selection to
the native backend. Authentication remains native to each CLI.

Guarded Pi launches use `--no-extensions -e <managed-extension>` and
`--tools mcp__cerebro__command`. `--no-skills` with explicit managed `--skill`
paths, `--no-context-files`, `--no-prompt-templates` and `--no-approve` isolate
role instructions and capabilities. Pi enforces tool gates, not an OS sandbox.
Writable children retain native tool defaults, configured MCP servers through
Pi's bundled MCP, CodeMode and tool-search extensions, and the repository's real
AGENTS instructions. Provider, authentication and UI settings
stay native. See Pi's [CLI resource controls](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md#resource-options).

The managed extension must be active before guarded work starts. Codex
explicitly selects its read-only sandbox on launch/resume and restricts
host-side capabilities. Claude removes native tools rather than treating an
auto-approval list as confinement.
Backend setup failures stop the command instead of silently weakening a role.

## Native completion and durable jobs

The supervisor's command tool automatically detaches long child commands. A
monitor in a separate process session owns the task and stores its job, PID,
output and final status under the Cerebro session. Disconnecting or timing out
the parent's tool call does not terminate the task. `status` and `jobs` let a
resumed supervisor find existing work instead of launching a duplicate.

`wait <job-id>` blocks on a Unix socket for completion or a new Jev notice.
`--after <notice-sequence>` acknowledges the last delivered notice and resumes
waiting for the next event. The monitor publishes the final status before
notifying waiters, so waiting after completion also works. There is no loop
polling the child's PID, status file or logs to schedule the supervisor. Missing
completion transport while a job still claims to be running is reported as a
failure.

Parents call this tool directly and remain blocked until an event arrives.
They do not use yielding execution wrappers, timed wakeups or progress-file
checks to monitor a pending child. User input can interrupt the wait; the
durable job remains available for an explicit correction or a resumed wait.

Backend adapters consume native events or stdout directly. Pi's RPC adapter
tracks tool/message events and pending steering, then waits for `agent_settled`
after automatic continuation is finished. Its `prompt` command uses native
`streamingBehavior: "steer"` when work is active. See Pi's [RPC lifecycle](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md#run-lifecycle).
Codex joins outstanding command items before handing back a completed turn.
Workers must join all finite background work and clean up servers they started
before their final handoff. Arbitrary detached OS processes are not a native completion
contract. A configured wall-clock limit bounds child work; paired transports also
have inactivity bounds.

`--watch` on execute/apply-review/doc-write enables the native pair transport
and its explicit steering FIFO. `--no-watch` opts out; `jev_enabled=1` makes
watching the default for development children. Watched commands must run through
the MCP command tool or a detached monitor that owns the completion socket.
Watching adds no post-turn idle window unless the user also selects `--pair`.
The native child stays open while classification or notice acknowledgment is
pending, then normal completion proceeds promptly. Missing key or socket
configuration is an explicit error. Network or protocol errors stop the watched
child without destructive cleanup, preserving resumable work; there is no silent
fallback.

Steering uses native child input APIs. An authorized execute restart retires the
native conversation while retaining the checkout, changes, branches and PRs.
The corrected task targets that retained checkout; destructive cleanup is
separate. Follow-up tasks use steering or cancellation, which terminates the
monitor and its descendants. Jev does not authorize these actions or change
requirements.

With `jev_enabled=1`, the shared `review` command assesses the native review
before returning its report, on every backend. Jev receives the session spec,
acceptance criteria, review text, diff and cited source excerpts. Evidence is
bounded and truncation is explicit. Typed validity/usefulness labels include
confidence and a cited excerpt; the original review remains intact. Uncertainty
and disagreement go to the supervisor. No label suppresses findings, changes
delivery authority or replaces required runtime verification. Changed inputs or
classification errors fail the assessment without advancing review state.

Every Jev call writes a private `.jev.jsonl` trace beside its child log or review
report. Request records contain the exact submitted state and questions;
response records contain the bounded HTTP body, status, elapsed time and any
transport/validation error. Request IDs link these records to `.scope.jsonl`
decisions or `.assessment.json` review assessments. Authorization headers are
never logged. A request without a response identifies an interrupted call.

## Durable state and identity

Cerebro owns a session ID independently of each backend's conversation ID.
Metadata records the backend, role and native conversation binding. Pi records
the absolute path to its native JSONL session file; Claude binds through its
user-prompt hook, and Codex through its native turn-completion notification.
Native bindings are captured as sessions are created, enabling same-conversation
resume after an interruption. Cerebro validates a recorded Pi session file before
resuming because Pi would otherwise create a new session for a missing or empty
file. An invalid resume fails; it does not silently start fresh work.

```text
$CEREBRO_HOME/
  .agents/skills/                  shared role and workflow skills
  .claude/skills/                  links to the shared skills
  learnings.md, pending-learnings.md
  overlays/                       user-owned prompt additions
  worktrees/<session>-<task-key>/  explicitly requested isolated checkouts
  sessions/<id>/
    metadata.json, transcript.jsonl
    spec.md, spec-history.jsonl
    plans/                        optional recorded plans
    audits/, children/            findings, native logs and steering records
    child-sessions.json           native child IDs and lifecycle status
    detached-jobs/                persistent ownership and completion records
    review-state/                 prior reviewed HEAD and report per repo/branch
```

The spec is the requirements of record. Replacing it archives the prior version.
Plans can adapt within the agreed requirements as facts or user input change.
An ordinary plan is a short list of possible commits, not a separate approval or
paired technical/readable artifact. `plan` and `audit` remain optional commands.
Repository instructions are respected; Cerebro does not bootstrap instruction
files or templates. `execute` defaults to the supplied checkout; `--worktree`
requests isolation and `--branch` selects or creates a branch without resetting
existing history. The parent chooses based on task relevance, existing edits and
concurrent work. Follow-ups use the announced checkout. The child record stores
its actual cwd and workspace selection/creation metadata, independently of native
conversation lifetime. Resume verifies checkout identity and refreshes the observed
branch without switching it. Missing or replaced checkouts fail explicitly.

Only incomplete children auto-resume, within the configured retention period.
Cleanly completed work gets a fresh child next time. A blocked child ends with a
question; `answer <child-id> <answer>` resumes that exact conversation. The
supervisor answers from the recorded contract or relays a real product decision.

## Jev scope notices and explanatory review

The child adapter batches direct native events for typed `Choice` classification
against the current spec, delegated task and adjustable plans. Jev returns
`in_scope`, `possible_deviation` or `uncertain`, confidence and the original cited
event. In-scope results stay silent. A possible deviation below the configured
threshold becomes an uncertain notice; a confident deviation requires a
supporting native event. Significant deviation or uncertainty sends a notice
through the job's completion socket while the child continues. Each notice
identifies the running job, exact steering FIFO and a monotonically increasing
sequence. The parent assesses it, optionally steers under existing permissions
and resumes with `wait <job-id> --after <notice-sequence>`.

Published notices preserve observed history across disconnects. The parent
assesses them against the current requirements; an older stored warning can be
dismissed and acknowledged without steering. A scope update does not withdraw
that historical notice.

All three backends feed the same Python HTTP Jev watcher. Pi's native TypeSafe
classification feature is unused, keeping scope notices and wait semantics
shared across backends. The classifier's score is advisory, not proof of correct
scope. The parent assesses the cited evidence and decides how to respond.

This control path uses direct native events and blocking notifications, without
a separate observing agent, native parent prompt injection or log polling. The
supervisor consumes cited exceptions instead of implementation logs. User changes
to the spec or plan update the classification context. If that context changes
during an in-flight request, the same event batch is reclassified before
publication. Classification has no mutation, restart or delivery authority and
cannot masquerade as a user requirement.

Jev defaults to disabled, model `jev-latest`, endpoint
`https://api.typesafe.ai/v1/systemone` and confidence threshold `0.8`.
`CEREBRO_JEV_API_KEY` or the private `jev_api_key` config value supplies its
credential. This classifier setting is independent of the three native role
model selectors. See [USAGE.md](USAGE.md#watch-development-scope-with-jev) for
per-task flags, notices and configuration.

After each authorized commit, the supervisor delegates a fresh review pinned to
its previous HEAD: `review <worktree> --base <previous-head> --explain`. The
reviewer follows `hashimoto-review`, producing both findings and notes that
explain behavior, ownership, design choices and failure/recovery paths. It offers
human review and continues authorized work without waiting for acceptance.

The guarded `hunk` bridge exposes Hunk's bundled review-skill path, safe session
inspection/navigation and additive comments. It cannot launch an interactive
diff/show, reload the user's window, clear notes or delete them. The human owns
the Hunk window; reviewers annotate it through its live-session API. If no live
window exists, the same explanatory notes appear inline with excerpts and exact
file/line anchors. Missing Hunk does not reduce the review or halt development.

Explicitly selecting `supervise` enables unattended operation within the user's
requested workflow phase. Upstream engineering and review practices apply
without a separate Cerebro suite or routine confirmation policy. Required checks
and significant findings still matter; source changes, commits, pushes, PRs,
merges, infrastructure and destructive actions follow user/repository authority.
A skill does not authorize additional workflow phases or publication. Cerebro
does not serialize competing mutations against a repository; the supervisor
must sequence them.

## Frontends and source boundaries

Terminal sessions support all three backends. ACP is a thin editor proxy for
Claude; it preserves native protocol capabilities, binds native session IDs and
pins the guarded supervisor role through a small native wrapper around the shared
supervisor skill. Cerebro's Pi and Codex backends use terminal interfaces; ACP
requests fail before session or project creation. The optional PTY MCP frontend
drives real terminal sessions and provides its own completion notifications;
it is distinct from the guarded command MCP server.

`bin/cerebro` resolves the library and dispatches. `lib/backend.sh` selects the
native adapter; `lib/commands/` owns workflows and read-only bridges; shared
prompts/config live in `lib/payloads/`; Python helpers own protocol parsing,
process lifecycle and native transport. No backend-specific workflow is
required. Run `bash tests/run.sh` before delivery; fixtures supplement native
runtime verification rather than replacing it.
