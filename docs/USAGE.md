# Using cerebro

Talk to Cerebro's supervisor in plain English; it delegates the work through
guarded commands. Choose a backend with `CEREBRO_BACKEND=pi|codex|claude`.
Pi is the library default; existing backend/model configuration takes precedence.
Each session and every child stay on the selected backend, including review and
verification. For what happens under the hood, see [ARCHITECTURE.md](ARCHITECTURE.md).

Pi requires version **0.99.2 or later** and **Node ≥ 22.19.0**. Install the
official [Pi CLI](https://pi.dev/) with:

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.99.2
```

Cerebro supplies a small managed extension using Pi's native MCP support and the
shared skills. No third-party MCP, subagent or permission extension is needed.
Authenticate through Pi's native provider configuration before delegating work.
Native provider, authentication and UI settings remain in use.
Writable Pi children keep native tool defaults and configured MCP servers through
Pi's bundled MCP, CodeMode and tool-search extensions. Supervisor and reviewer
sessions load only Cerebro's
guarded command server.

## Sessions

```bash
cerebro                       # mint a new session, drop into the chat
cerebro --resume <id>         # resume a specific session
cerebro --resume              # resume the most recently touched session
cerebro list                  # list sessions, newest first
cerebro detach --output <path> -- <child-command> [...]
                              # launch a child outside harness task cleanup
cerebro jobs                  # list persistent detached child jobs
cerebro wait <job-id> [--after <notice-sequence>]
                              # wait for completion or a new scope notice
cerebro cancel <job-id>       # stop a detached job and its descendants
```

## Develop in small commits

Describe the outcome and constraints. The supervisor keeps a short plan of
possible commits, delegates development into isolated worktrees and adjusts the
plan as facts or your input change within the agreed requirements. You do not
need to approve each plan revision or read a second companion plan.

Commit, push, PR and merge authority comes from your instructions and the
repository's rules. If commits are authorized, each is a coherent, workable
increment with appropriate engineering verification. If they are not, the worker
leaves the requested changes for inspection. Existing repository instructions
are read and respected; Cerebro does not create or modify `AGENTS.md`/`CLAUDE.md`
as a bootstrap step.

After each authorized commit, a fresh read-only reviewer follows
`hashimoto-review` and explains that commit's behavior, architecture, choices,
risks and failure paths. Pin the range to the preceding HEAD:

```bash
cerebro review /absolute/task-worktree --base <previous-head> --explain
```

The report includes findings and a guided explanation. Human review is offered;
a lack of acceptance does not pause the next authorized step. Significant
findings are corrected within scope, followed by affected verification and a
fresh review of the next commit. A real product decision or missing authority
still needs resolution.

Use the worktree announced by `execute` for review, corrections, verification and
documentation. The user's main checkout remains separate. The `engineering`
skill guides verification through the changed runtime boundary; a static review
cannot prove runtime behavior. Report any verification gap accurately.

### Review notes in Hunk

When Hunk is available, the reviewer offers the exact command for you to run in
another terminal from the task worktree, for example:

```bash
hunk diff <previous-head>...<new-head>
```

You own that live Hunk window. Cerebro uses a guarded bridge to inspect the
session, navigate the diff and add explanatory notes without changing source
files. It first reads Hunk's bundled review instructions:

```bash
cerebro hunk skill path hunk-review
cerebro hunk session list --json
```

The bridge permits safe session inspection/navigation and additive comments.
Interactive `diff`/`show`, reload, deletion and clearing are unavailable through
it. Existing notes are preserved. Without a live Hunk window, the review still
provides the prepared notes inline with relevant excerpts and exact file/line
anchors, and offers the command to open the review. Work does not wait for that
window or human acceptance merely to continue.

### Optional plans and audits

`plan` still records a plan you wrote; `plans` lists or removes recorded plans.
`audit` still checks a plan against the code when useful or requested. These
commands do not introduce a mandatory approval, audit round or paired
technical/readable-plan workflow.

## Use supervise for unattended work

Explicitly invoke the upstream `supervise` skill when you want an unattended
handoff. State the requested workflow phase, outcome, scope and delivery
permissions. For example: "Use supervise to implement and verify this change;
you may commit and open PRs, but leave merging to me."

The skill applies within that authorized phase, delegates the work and tracks
real dependencies. It does not require routine confirmation to continue already
authorized work. It also does not add earlier/later phases or grant commit,
push, PR, merge, infrastructure or destructive authority that you have not
given. Cerebro does not impose a second suite or audit-gate policy on top of it.
Jev watching is optional and can be selected per delegated development task.

## Ask about a repo

"What does HEAD look like vs main?", "is CI green on the PR?", "where
is the retry logic?" — the orchestrator answers these without spawning
an agent, through guaranteed read-only bridges (`cerebro git`, `gh`,
`grep`, `read`, `ls`) that allow-list every verb and flag. It also
checks `cerebro recall` — a literal search across all your past
sessions' transcripts and agent logs — before re-asking you something
you already answered in a prior session.

## Watch development scope with Jev

`execute`, `apply-review` and `doc-write` accept `--watch` or `--no-watch`.
Watching is off by default; `jev_enabled=1` enables it for development children
unless the parent opts out. The parent can also opt in for an individual task.
`--watch` enables the native pair transport so an explicit FIFO can deliver
steering while the task runs. It adds no post-turn idle window unless `--pair`
is also explicitly selected. The child stays open while classification or
notice acknowledgment is pending, then completes promptly.

Provide a private `CEREBRO_JEV_API_KEY` or `jev_api_key` in
`$CEREBRO_HOME/config.json`. The defaults are model `jev-latest`, endpoint
`https://api.typesafe.ai/v1/systemone` and confidence threshold `0.8`. Jev is a
classifier for scope and review assessment; it does not change your supervisor,
implementation or review model settings. See [Configuration](#configuration)
for all Jev options.

The child adapter batches direct native events for a cheap typed `Choice`
classification against the current session spec, delegated task and adjustable
plans. Each result is `in_scope`, `possible_deviation` or `uncertain`, with
confidence and the original cited event. In-scope work stays silent. A
`possible_deviation` below the confidence threshold is presented as `uncertain`;
both significant deviation and uncertainty notify the waiting parent while the
job continues. A confident deviation notice requires a supporting native event.
Updating the spec or plan changes the scope context. If it changes while a
classification request is in flight, the watcher reclassifies that batch against
the new context before publication.

Every call writes a private `children/<child>.jev.jsonl` trace containing the
submitted state and questions, response body, HTTP status, elapsed time and any
error. Authorization headers are excluded. Request IDs link these records to
the existing `.scope.jsonl` decisions. This includes failed classifications;
an unfinished request remains visible after an interrupted process.

With `jev_enabled=1`, `review` also checks the native review's validity and
usefulness against the spec, acceptance criteria, diff and cited source excerpts.
The returned report includes advisory labels and links to `.assessment.json`
and `.jev.jsonl` files beside it. Its original findings remain intact. Large
inputs are bounded with explicit truncation, and low-confidence judgments are
reported as uncertain. The supervisor assesses conflicts and missing evidence;
the labels do not authorize fixes or waive verification. A classification error
or changed input returns an explicit failure while retaining the original
report. A development task's `--no-watch` does not disable later review checks.

Execute restart removes a fresh task's worktree and branch. It refuses teardown
when the child switched away from a pinned branch, when that local or origin
branch predates the worktree, or when the initial branch inventory is unavailable.
The retained work remains available for inspection. Sequence competing mutations
in the same repository; the initial inventory is not a lock on branch names.

Use the guarded MCP command tool for watched tasks, for example:

```json
{"argv":["execute","/absolute/repo","--prompt","Build the CSV export","--watch"]}
```

A scope notice includes the still-running job ID, explicit steering FIFO and
monotonically increasing notice sequence. The parent assesses the cited evidence
against the current requirements and decides whether to steer, cancel or
continue. Published notices remain historical evidence across disconnects;
changing scope does not withdraw them. The parent may dismiss an older warning
against the current spec and acknowledge it without steering. It acknowledges
the notice and re-arms the wait through the same command tool:

```json
{"argv":["steer","/explicit/child.steer.fifo","Return to the agreed export scope"]}
{"argv":["wait","<job-id>","--after","<notice-sequence>"]}
```

An explicit CLI launch must run through `detach`, which owns the completion
socket. The output must be inside the active Cerebro session, for example:

```bash
cerebro detach --output /absolute/session/export.out -- execute /absolute/repo --prompt "Build the CSV export" --watch
```

Watched direct CLI use without MCP or a detached monitor fails with an explicit
socket error. A missing Jev key also fails explicitly. Network or protocol errors
stop the watched child with a clear failure, preserving resumable work without
destructive cleanup. There is no silent fallback. Notices wake the waiting parent
through the completion transport; there is no separate observing agent, parent
prompt injection or log polling.

Jev has no mutation or delivery authority. A notice is evidence for the parent,
not a user instruction or automatic restart request. Steering stays within the
agreed requirements. Restart cleanup is reserved for a fresh `execute` task when
abandonment of its isolated branch, PR and worktree is preauthorized; follow-up
`apply-review`/`doc-write` tasks use steering or cancellation. A user requirement
change is recorded in the spec and plans before the parent continues.

## Drive it from your editor (ACP)

With the Claude backend, `cerebro acp` speaks the
[Agent Client Protocol](https://agentclientprotocol.com), so an ACP-aware editor
such as Zed can drive the supervisor turn by turn. Cerebro relays native protocol
features and preserves the same guarded role; available tools remain constrained
by that role. Cerebro's Pi and Codex backends use terminal sessions; ACP requests
for either backend fail before session or project creation.

For each editor session the proxy:

1. Mints a durable Cerebro session and a Cerebro-owned project directory.
2. Spawns `claude-agent-acp` with the session environment.
3. Pins the guarded supervisor role through a small Claude ACP agent wrapper
   around the shared supervisor skill.
4. Relays JSON-RPC with session ID remapping and records the native conversation
   ID for subsequent load/resume.

Your repo is never written to from the ACP path: it is passed to the child as an
ACP `additional_directory`, and the child's session cwd is the cerebro-owned
project dir.

### Register it in Zed

Add a [custom agent server](https://zed.dev/docs/ai/external-agents#custom-agents)
to your Zed `settings.json`, selecting Claude for this launcher:

```jsonc
"agent_servers": {
  "Cerebro": {
    "type": "custom",
    "command": "cerebro",
    "args": ["acp"],
    "env": { "CEREBRO_BACKEND": "claude" }
  }
}
```

Zed launches `cerebro acp` once (long-lived); each thread's cwd comes from the
editor. Open a Cerebro thread in a repo and describe the change and delivery
authority. A delegated child binds to the session (visible in `cerebro list`)
and reports back through the editor. Restart Zed and resume the
thread — the upstream conversation reopens.

### Restart the proxy after a config change

`cerebro acp` reads `$CEREBRO_HOME/config.json` and the `CEREBRO_*` env vars
once at startup, then reuses the resolved values for the lifetime of the
proxy (and for every per-session upstream child it spawns). To pick up a
config change without restarting the editor, run

```bash
cerebro acp restart
```

This signals the running proxy (for your current `CEREBRO_HOME`) to exit;
the editor respawns `cerebro acp` and the next session materialises with
fresh config. Idempotent — a no-op when no proxy is running.

### Backends and dependencies

* **claude:** uses `@agentclientprotocol/claude-agent-acp` via `npx`, which
  needs **Node ≥ 22** + `npx` on PATH. Set `CEREBRO_BACKEND=claude` (and, for a
  gateway, `CEREBRO_CLAUDE_BASE_URL`).
* **pi and codex:** supported in terminal sessions; ACP is unavailable.

ACP needs **Python ≥ 3.10** plus the `agent-client-protocol` package. `cerebro
acp` prefers Homebrew's `python3` (`/opt/homebrew/bin/python3`) — macOS system
python3 is 3.9, too old — and auto-installs the SDK to your user site
(`pip install --user --break-system-packages`) on first run if it's missing.
Install them yourself if you prefer:

```bash
brew install python
/opt/homebrew/bin/python3 -m pip install --user --break-system-packages agent-client-protocol
```

### Scope

ACP is editor-driven and turn-by-turn. Development remains delegated through the
same guarded command tool, including optional Jev watching. `cerebro list` shows
ACP-created sessions and `cerebro --resume <id>` reopens their native conversation.
The editor's upstream connection still controls the duration of an individual
parent turn; persistent child jobs can be rediscovered with `status`/`jobs`.

## Drive it from another agent (PTY MCP)

`cerebro cerebro-mcp` starts a **generic terminal MCP server**
(`lib/python/cerebro_mcp_server.py`) over stdio on the official `mcp` Python SDK.
It owns long-lived pseudo-terminals in its own process and exposes them as MCP
tools, so a controller — another agent, an editor, a test harness — can spawn an
**interactive TTY program**, send it input, and get resumed on a real event
(idle / regex match / child exit) **without polling**. cerebro is one client;
the surface drives any interactive program (anything that needs a real PTY and
rejects pipes), not just cerebro.

This is what lets an agent controller autonomously drive cerebro's interactive
chat (which requires a genuine TTY): the MCP server holds the PTY across turns,
and the controller issues one blocking `cerebro_wait` per event boundary instead of
polling.

### Tools

* `cerebro_spawn({command, args?, cwd, env?, cols?, rows?}) -> {sessionId, pid}` —
  allocate a PTY and exec the program (no shell; `command` resolved via PATH).
  `cwd` is required. **Executes arbitrary code — shell-equivalent.**
* `cerebro_send({sessionId, text?, key?}) -> {ok, bytesWritten}` — write raw UTF-8
  `text` and/or a `key` (`enter`, `ctrl-c`, `ctrl-d`, `esc`, `tab`, `up`, `down`,
  `left`, `right`, `home`, `end`, `backspace`, `delete`, `pageup`, `pagedown`).
  `text` is sent first, then the key (e.g. `"world"` then `enter`). Safe to call
  while a `cerebro_wait` is waiting on the same session.
* `cerebro_wait({sessionId, idleMs?=1500, match?, timeoutMs?=30000}) -> {event, …,
  tail, cursor}` — **the event primitive**. Blocks server-side and returns on
  the first of: `exit` (child died; `exitCode` set), `match` (regex found in text
  appended since this call started), `idle` (output was seen AND the session has
  been quiet for `idleMs`), or `timeout` (`timeoutMs` elapsed, capped at 120000).
  `tail` is the normalized text appended since this call started; `cursor` is the
  new read cursor. Re-issue after a `timeout` to keep waiting.
* `cerebro_read({sessionId, sinceCursor?, tailLines?}) -> {text, cursor, ended}` —
  non-blocking incremental read (since a cursor from a prior wait/read) or last-N
  lines.
* `cerebro_status`, `cerebro_resize`, `cerebro_kill({signal?})`, `cerebro_close` (retire a
  session; idempotent), `cerebro_list`.

### Usage pattern (event-driven, no polling)

Capture the cursor, send input, then **one `cerebro_wait` per event boundary**. A
fast program may finish before the `cerebro_wait` even starts watching — in that case
the wait returns `exit` with an empty `tail`, and the response text is still in
the buffer: read it with `cerebro_read(sinceCursor=<the cursor you captured before
the send>)`. Output is ANSI-normalized (escape sequences stripped; `\r\n` line
endings kept; `\r`-spinner frames collapsed to their final state).

```text
w1 = cerebro_wait(sessionId, idleMs=800)        # blocks until the prompt appears
c  = w1.cursor
cerebro_send(sessionId, text="do the thing", key="enter")
w2 = cerebro_wait(sessionId, idleMs=1500)        # blocks until it goes quiet / exits
reply = cerebro_read(sessionId, sinceCursor=c)   # full text since before the send
```

Many agents can drive many sessions at once: each session has its own reader
thread and condition lock, and every tool offloads its blocking work to a
threadpool worker so the MCP event loop stays free for concurrent calls.

### Register it with an MCP client

For Claude Code (available across repos with `-s user`; `cerebro` must be on
PATH):

```bash
claude mcp add cerebro -s user -- cerebro cerebro-mcp
```

Then refresh the session so the `cerebro_*` tools load. The server reads no
server-wide config and is owned by the MCP client, so — unlike `cerebro acp` —
there is **no `cerebro-mcp restart`**: if it misbehaves, reconnect via the client
(session restart), not a cerebro subcommand. PTY children self-clean (SIGHUP on
master close + an `atexit` SIGTERM) when the server exits.

### Dependencies

Python **≥ 3.10** plus `mcp` **≥ 2, < 3**. `cerebro cerebro-mcp` prefers Homebrew's
`python3` (`/opt/homebrew/bin/python3`) — macOS system python3 is 3.9, too old —
and auto-installs the SDK to your user site on first run if it's missing:

```bash
brew install python
/opt/homebrew/bin/python3 -m pip install --user --break-system-packages 'mcp>=2,<3'
```

## Resume and interrupted work

Sessions are durable. `cerebro --resume <id>` (or the most recent session) drops
you back into the same conversation, with the session spec, plans,
review state, and transcripts intact on disk.

Closing the parent mid-run loses nothing. Long-running children use
`cerebro detach`, which records each job under the session and keeps it alive
outside the agent harness's process group. On "continue" the orchestrator
checks `cerebro status`: the command tool automatically creates a durable job
for long child commands and waits on its completion socket. A live detached job is allowed to finish rather than
being duplicated, and a completed job remains discoverable through `cerebro
jobs`. `cerebro wait <job-id>` blocks on completion or a new scope notice;
use `--after <notice-sequence>` to acknowledge a received notice and wait again.
No log or child-state polling is needed. `cerebro cancel <job-id>` deliberately
stops the monitor and its full descendant process tree.

If the child process itself is interrupted, its native conversation binding was
persisted when the session was created. Pi's binding is an absolute JSONL session
file path; Cerebro validates the file before resume so a missing or empty file
cannot silently start fresh work. The orchestrator resumes each interrupted
child via `--resume` instead of duplicating work. Stored bindings stay resumable
for `CEREBRO_CHILD_SESSION_TTL` seconds
(default 24h), but normal child launches only auto-resume children still
marked in-flight. Once a child finishes cleanly, the next sub-agent starts
a fresh provider conversation even if it runs on the same repo and branch.

A child that hits a genuine blocker doesn't guess and doesn't die: it
**pauses with a question** as its closing message. The orchestrator
answers from the spec or past sessions when the record settles it, and
only relays to you when the decision is genuinely yours. The closing
message prints the child session id, and
`cerebro answer <child-session-id> "<answer>"` resumes that same child
exactly where it stopped.

## Teach it your preferences

When you reveal a general preference — directly ("always keep diffs
small") or by repeatedly correcting in the same direction — the
orchestrator records the signal, and once the evidence is clear (one
explicit directive, or the same signal twice) consolidates it into a
small global `learnings.md` that the supervisor reads in
**every future session**, across all repos. Ambiguous signals get a
clarifying question first. `cerebro learnings` (ask the orchestrator)
prints the active set.

For tuning a specific prompt surface that `learnings.md` cannot reach —
a child role prompt or the review grader — ask the orchestrator to set a
**local overlay**. User-owned markdown files under
`~/.cerebro/overlays/` (`system`, `execute`, `apply-review`,
`doc-write`, `grader`, plus the `meta-*` targets) tune their corresponding
prompt surfaces. They are local, never materialised, and survive `git pull`, so
you can adjust any prompt surface without forking. `cerebro overlay
show` lists them.

## Improve the harness from its own traces

`cerebro improve <cerebro-source-repo>` runs the configured reviewer as a read-only
analysis agent over the accumulated trace corpus under `~/.cerebro`,
mining problems that **recur across runs** and proposing the smallest
fixes back into the harness. It only proposes — findings end in a
`HILL CLIMB:` verdict, and the orchestrator routes each accepted item
into a local overlay or `learnings.md` (or, if you maintain the cerebro
source, an upstream PR). Nothing rewrites the harness unsupervised; run
it on request. Successful runs are recorded chronologically in
`~/.cerebro/improvement-history.json`. Every `meta_horizon` successful fast
runs, or whenever `--meta` is passed, a second read-only pass reviews that
history and can propose a change to one of the local `meta-*` overlays. The
history records findings and verdicts, not acceptance decisions or causal
utility deltas.

## Scope and authority

The requirements are the contract. A short plan can adapt to new facts and user
input within them. A requirement change comes from you, not from a classifier
or an implementation assumption; when the recorded spec changes, prior versions
remain in its history. Material uncertainty about your intended outcome is
returned to you, while routine engineering choices stay delegated.

Native permissions and the guarded command tool keep repository mutation in
writable children. Reviewers inspect and annotate without source writes. Human
review is an invitation to understand and steer the work, not a routine
acceptance gate. Publication, merge, infrastructure and destructive actions
still depend on your authorization and applicable instructions.

Cerebro reuses the pinned upstream `engineering`, `supervise` and
`hashimoto-review` skills rather than maintaining a parallel workflow policy.
See [ARCHITECTURE.md](ARCHITECTURE.md#shared-skills-small-backend-adapters) for
provenance. Optional Jev watching supplies cited scope notices; the parent owns
the response and all authorization decisions.

## Install details

The installer clones into `~/.local/share/cerebro` (override with
`CEREBRO_SRC`), symlinks `cerebro` into `~/bin` (override with
`CEREBRO_BINDIR`), and adds that directory to your PATH if needed.
Re-running it updates the clone in place. To pin a ref, set
`CEREBRO_REF`.

Prefer to manage it yourself:

```bash
git clone https://github.com/aminmarashi/cerebro.git ~/.local/share/cerebro
ln -s ~/.local/share/cerebro/bin/cerebro ~/bin/cerebro   # ~/bin must be on PATH
```

To uninstall without a working `cerebro-uninstall` symlink:

```bash
curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/uninstall.sh | bash
```

## Configuration

Every option below can be set two ways: as an env var (`CEREBRO_*`) or as a
key in the options file at `$CEREBRO_HOME/config.json` (alongside the model
catalog at `$CEREBRO_HOME/models-config.json`). **Env vars take precedence**
over the file, and the file takes precedence over the hardcoded default
(`env > config.json > default`). The file is handy for options you want set on
every run without polluting your shell; use env vars for per-invocation
overrides. Keys are lower-case option names without the `CEREBRO_` prefix
(e.g. `backend`, `review_model`, `pair_idle`). Unknown keys are ignored, and a
missing/invalid file is not an error. `CEREBRO_HOME` itself is env-only (the
file lives under it).

```json
{
  "backend": "codex",
  "supervisor_model": "",
  "model": "",
  "review_model": "",
  "jev_enabled": 0,
  "timeout": 0,
  "pair_idle": 60,
  "child_session_ttl": 86400,
  "debug": 0
}
```

Options and their defaults (all optional):

| option (key) | env var | meaning | default |
|-----|-----|---------|---------|
| `home` | `CEREBRO_HOME` | base dir for all state (env-only, not read from config.json) | `~/.cerebro` |
| `backend` | `CEREBRO_BACKEND` | CLI for the supervisor and all children: `pi`, `codex`, `claude` | `pi` |
| `supervisor_model` | `CEREBRO_SUPERVISOR_MODEL` | native model ID for the supervisor, including resume and ACP | backend default |
| `model` | `CEREBRO_MODEL` | native model ID for implementation children | backend default |
| `review_model` | `CEREBRO_REVIEW_MODEL` | native model ID for review/audit/verify/improve on the same backend | `model`, or backend default |
| `claude_base_url` | `CEREBRO_CLAUDE_BASE_URL` | optional Anthropic-compatible endpoint for the claude backend (e.g. a local Ollama `/v1/messages` server, or any proxy). empty = the claude.ai subscription `claude` is logged into. when set, every configured role model must name a model the endpoint serves | empty (subscription) |
| `claude_auth_token` | `CEREBRO_CLAUDE_AUTH_TOKEN` | bearer token for the optional Claude gateway | empty (`ollama` placeholder for a local gateway) |
| `jev_enabled` | `CEREBRO_JEV_ENABLED` | enable review assessments and default development watching (`0`/`1`); development `--watch`/`--no-watch` flags apply to that task | `0` |
| `jev_api_key` | `CEREBRO_JEV_API_KEY` | private Jev credential; required for watched tasks and review assessments | empty |
| `jev_model` | `CEREBRO_JEV_MODEL` | scope/review-classification model | `jev-latest` |
| `jev_endpoint` | `CEREBRO_JEV_ENDPOINT` | typed classification endpoint | `https://api.typesafe.ai/v1/systemone` |
| `jev_confidence` | `CEREBRO_JEV_CONFIDENCE` | minimum confidence for deviation/review labels; lower-confidence judgments become uncertain | `0.8` |
| `timeout` | `CEREBRO_TIMEOUT` | wall-clock cap (s) per child call | `0` (no cap, so e2e runs and CI waits are never killed) |
| `child_idle_timeout` | `CEREBRO_CHILD_IDLE_TIMEOUT` | optional parser inactivity bound (s); native transports own completion/stall handling | `0` (disabled) |
| `child_session_ttl` | `CEREBRO_CHILD_SESSION_TTL` | how long (s) a stored child id stays resumable | `86400` (24h) |
| `pair_idle` | `CEREBRO_PAIR_IDLE` | steering window (s) after each turn when `--pair` is explicit; watching alone adds no idle window | `60` |
| `pair_stall` | `CEREBRO_PAIR_STALL` | paired/watched native stream inactivity bound (s) when no tool is running; unpaired calls await native completion | `180` |
| `pair_stall_busy` | `CEREBRO_PAIR_STALL_BUSY` | paired/watched native stream inactivity bound (s) while a tool runs | `450` |
| `pair_stall_retries` | `CEREBRO_PAIR_STALL_RETRIES` | max restart attempts for a stalled paired child | `2` |
| `pair_stall_backoff` | `CEREBRO_PAIR_STALL_BACKOFF` | base (s) for the exponential restart backoff | `5` |
| `pi_cmd` | `CEREBRO_PI_CMD` | Pi executable | `pi` |
| `claude_cmd` | `CEREBRO_CLAUDE_CMD` | claude executable | `claude` |
| `codex_cmd` | `CEREBRO_CODEX_CMD` | codex executable | `codex` |
| `playwright_isolated` | `CEREBRO_PLAYWRIGHT_ISOLATED` | isolate the @playwright/mcp browser profile per child (in-memory) so concurrent browser-capable children don't collide on Chromium's SingletonLock; set `0` to keep the shared persistent profile (sequential-only) | `1` |
| `overlay_cap` | `CEREBRO_OVERLAY_CAP` | max chars in a single harness overlay file | `4000` |
| `meta_horizon` | `CEREBRO_META_HORIZON` | fast-loop runs between meta-skill (`--meta`) runs | `2` |
| `debug` | `CEREBRO_DEBUG` | `1` for verbose logs | `0` |

Two deliberate limits to know about:

* **Interactive-only.** `cerebro` requires a genuine interactive TTY on
  stdin and stdout. Any controller that allocates a real PTY works -- a
  shell, a terminal multiplexer, an editor, or another agent such as
  Codex launched with `tty: true`. Pipes, redirected input/output, and
  cron-style launches are rejected. The sub-agents the orchestrator
  spawns are exempt.
* **No concurrency control.** cerebro won't stop you from running two
  mutating operations against the same repo at once, within or across
  sessions — sequence your own mutating work.

### Using a local model (Ollama, any Anthropic-compatible gateway)

The `claude` backend normally uses the claude.ai subscription `claude`
is logged into. Point it at a local model server instead by setting
`CEREBRO_CLAUDE_BASE_URL` and a model the server serves — cerebro
exports the endpoint into every spawned `claude` (orchestrator and
children), unsets `ANTHROPIC_API_KEY` so a logged-in subscription can't
hijack the run, and pins the background "haiku" model to the same name so
housekeeping calls also stay local:

```bash
ollama serve                       # local server on :11434
CEREBRO_BACKEND=claude \
CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
CEREBRO_SUPERVISOR_MODEL=qwen2.5-coder:14b \
CEREBRO_MODEL=qwen2.5-coder:14b \
cerebro
```

Leave `CEREBRO_CLAUDE_BASE_URL` unset to keep using the subscription.
For an authenticated gateway, set `CEREBRO_CLAUDE_AUTH_TOKEN` to the
real key (the default is a placeholder local no-auth servers ignore).
Claude Code can't infer the context window for a model id it doesn't
recognize (anything not a built-in Claude alias) and falls back to 200k.
If the catalog entry for a role's selected model declares a `contextTokens`
field (see "Model catalog" below), cerebro also exports
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>` into every spawned `claude` so
auto-compaction doesn't fire at the 200k default on a larger-window model.
Claude Code may still cap that value at its assumed window for the id (the
status line can read 200k); for direct `claude --model <id>` launches use
`cerebro model-env <id> [--no-compact]` to print the same exports, with
`--no-compact` as the escape hatch that forces the true window at the cost
of disabling compaction.

### Role model selection

Configure each role separately in `$CEREBRO_HOME/config.json`, for example:

```json
{
  "backend": "codex",
  "supervisor_model": "gpt-6-astra",
  "model": "gpt-6.1-sol",
  "review_model": "gpt-6-astra"
}
```

`CEREBRO_SUPERVISOR_MODEL` selects the supervisor for new sessions,
resume and editor launches. Leaving it empty uses the backend's configured
default. `CEREBRO_MODEL` selects implementation children. A supervisor model
change does not change either child default.

Review, audit, verification and improvement use the session's backend, with fresh
role-scoped context. `CEREBRO_REVIEW_MODEL` optionally selects another model on
that backend; it otherwise inherits `CEREBRO_MODEL`. Leaving both child settings
empty uses the native backend's configured default. `--model` overrides the model for one
child command. Changing the model never changes the backend.

Pi accepts native `provider/id` or fuzzy model IDs, with an optional `:<thinking>`
suffix; an unset model preserves Pi's selected/default model. Codex and Claude
accept their own native IDs. Cerebro does not infer a backend from punctuation.
Keep the model catalog relevant to the selected backend, and choose a
vision-capable model when runtime verification needs screenshots. Resuming a
session restores its recorded backend, regardless of the current
`CEREBRO_BACKEND` default. See Pi's [model options](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md#model-options)
for its native selection syntax.

### Model catalog

`cerebro models` prints the catalog you maintain at
`$CEREBRO_HOME/models-config.json`. Each entry has an `id` (the exact
native model ID passed to a subcommand's `--model` flag, same shape
as `CEREBRO_MODEL`), a `capabilities` list (an open set of present tags;
`vision` = multimodal image input, the one that matters for reading browser
screenshots during `verify`), a free-text `description` for judgement
the orchestrator reasons about (context window, reasoning depth, cost), and
an optional integer `contextTokens` -- the model's context-window size in
tokens. When the claude backend runs behind a custom endpoint
(`CEREBRO_CLAUDE_BASE_URL`), cerebro exports `contextTokens` as
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` so Claude Code doesn't fall back to its
200k default for an unrecognized id; `cerebro model-env <id>` prints the same
export for a direct `claude --model <id>` launch.

```json
{
  "models": [
    { "id": "github-copilot/gemini-3.1-pro-preview",
      "capabilities": ["vision", "tools", "thinking"],
      "contextTokens": 256000,
      "description": "Strong generalist; smaller context window." },
    { "id": "minimax/minimax-m3",
      "capabilities": ["vision", "tools", "audio"],
      "contextTokens": 512000,
      "description": "Vision + audio; use for screenshot/visual verification." }
  ]
}
```

`cerebro model-env <id> [--no-compact]` prints shell `export` lines that tell
Claude Code the model's real context window, for use before a direct
`claude --model <id>` launch against a custom endpoint:

```bash
eval "$(cerebro model-env glm-5.2:cloud)" \
  && claude --model glm-5.2:cloud --dangerously-skip-permissions
```

By default it exports `CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>` (keeps
auto-compaction, but Claude Code may cap it at its assumed window for the id
and the status line can still read 200k). `--no-compact` exports
`CLAUDE_CODE_MAX_CONTEXT_TOKENS=<tokens>` and `DISABLE_COMPACT=1` instead --
the documented override that makes `/context` reflect the true window, at the
cost of disabling auto-compaction. A model with no `contextTokens` (or an
unknown id) prints a note and no exports, so the `eval` is a safe no-op.

The orchestrator reads this catalog with `cerebro models` and chooses a
model per task -- e.g. routing `cerebro verify` to a `vision`-capable model
when the default review model lacks vision, or fanning a `cerebro review`
across several models by calling it once per catalog entry with `--model`.
A missing catalog leaves model selection to the explicit settings or the native
backend default.

## Session state

Everything durable is a plain file under `$CEREBRO_HOME` (default
`~/.cerebro/`), and the orchestrator will happily hand you paths to
open in your editor:

```
~/.cerebro/
  .agents/skills/                    # shared supervisor, worker and workflow skills
  .claude/skills/                    # links to the same skill files
  learnings.md                       # confirmed preferences read by the supervisor
  overlays/<target>.md               # user-owned prompt overlays (append onto shipped prompts)
  worktrees/<ckey>/                  # isolated per-task execute worktrees
                                     #   (GC stale ones with `cerebro worktrees cleanup`)
  sessions/<id>/
    metadata.json                    # backend, role and native conversation binding
    detached-jobs/                   # job ownership, result and final status
    spec.md                          # current session spec (requirements of record)
    spec-history.jsonl               # every prior spec version
    plans/                           # optional recorded plans
    children/                        # native logs of every child + review findings
    audits/                          # independent plan-audit findings
    improvements/improve.md          # latest `cerebro improve` hill-climbing findings
    review-state/                    # per-repo last-reviewed SHA
```

The native session bindings and completion ownership are covered in
[ARCHITECTURE.md](ARCHITECTURE.md#durable-state-and-identity).
