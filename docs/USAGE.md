# Using cerebro

Talk to Cerebro's supervisor in plain English; it delegates the work through
guarded commands. Choose a backend with `CEREBRO_BACKEND=opencode|codex|claude`.
OpenCode requires V2, minimum 2.0.19; V1 is not supported. Each session and every
child stay on the selected backend, including review and verification. For what
happens under the hood, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Sessions

```bash
cerebro                       # mint a new session, drop into the chat
cerebro --resume <id>         # resume a specific session
cerebro --resume              # resume the most recently touched session
cerebro --observe [<id>]      # watch-and-steer-only session for another
                              #   session's live paired children
cerebro list                  # list sessions, newest first
cerebro detach --output <path> -- <child-command> [...]
                              # launch a child outside harness task cleanup
cerebro jobs                  # list persistent detached child jobs
cerebro wait <job-id>         # wait for a detached job's completion
cerebro cancel <job-id>       # stop a detached job and its descendants
```

`cerebro --observe` opens a separate chat for watching another session's live
paired children. It waits for live activity, then reads substantial batches
through `observe` and compares them with the approved spec and plan. Its guarded
commands allow observation and steering, never repository edits or new
requirements. Pass a target session ID, or omit it to select the most recently
active other session with paired children. Ctrl-C cancels the initial wait.

Authorize autosteering if you want the observer to correct drift unattended.
Restart needs explicit or prior permission to abandon the task and remove its
isolated branch, PR and worktree. Observer messages remain marked `[observer]`;
they cannot change the spec. A cheap classifier using jev and typesafeai is a
separate future change.

## Ship a feature (the core loop)

Describe the change and the repo. The orchestrator:

1. Records what you asked for as the **session spec** — the
   requirements of record (see [Guardrails](#guardrails-and-autonomy)).
2. Drafts a plan into the session's `plans/` dir. Alongside the
   detailed technical plan it also writes a plain-English **companion**
   (`<name>-readable.md`) — the same plan with the dense code references
   stripped — and the path it gives you is the companion's; the
   companion links back to the technical plan, which stays the source of
   truth that gets executed.
3. Waits for your explicit **"go"**. For high-blast-radius changes (many
   files, shared modules, public APIs, schemas, auth paths), it then
   **audits** the technical plan once against the actual code before execution
   and folds in valid findings. User corrections apply directly.
4. Executes the plan in a sub-agent running in an **isolated git
   worktree** of the repo (under `$CEREBRO_HOME/worktrees/`), never your
   live checkout: it fetches the base branch, creates a feature branch,
   implements, commits, pushes, and opens a PR via `gh` — all inside the
   worktree. On success it announces the worktree path, which the
   orchestrator then uses as the repo argument for that task's review /
   apply-review / doc-write. Worktrees persist between runs; stale ones
   are reclaimed with `cerebro worktrees cleanup`.
5. Runs independent read-only review against the diff, summarises the findings,
   applies the in-scope important ones, and loops review →
   apply-review until the in-scope findings are resolved. Re-reviews inspect
   changes since the last review so the loop stays cheap. Out-of-scope or gold-plating findings are named to you, not
   silently applied.
6. **Verifies the change end to end by actually using the running
   app** — Playwright-driven where possible, or manual testing with
   you when it can't be automated. Static review and unit tests never
   count as "done" on their own.
7. Optionally updates docs on the same branch (`doc-write`).

First time it touches a repo with no `AGENTS.md`/`CLAUDE.md`, it adds
them from the user-editable templates at `~/.cerebro/templates/` as a
separate first commit (defaults: Conventional Commits, ≤ 80-char
subjects, `feat/`-style branches, no commits and no DB/infra changes
without an explicit ask). It never overwrites existing files.

## Ship a large change (stacked PRs)

Give it a spec too big for one coherent PR and it decomposes the work
into an **ordered suite of plans — one PR each**, stacked so each PR
branches off the previous one. Every step must satisfy the
**workable-state invariant**: each PR is independently shippable and
leaves the app building, green, and fully working — merging the stack
one PR at a time never leaves the app broken at any boundary. If the
spec can't be split that way, it says so and proposes a different cut
instead of emitting breaking plans.

You approve the decomposition once; it then executes the suite
autonomously. Each step is gated by a **checkpoint**: an independent review
fed the plan's acceptance criteria (verdict line `ACCEPTANCE CRITERIA:
MET` / `NOT MET`), zero important in-scope findings, *and* an
end-to-end check of that step's user flow against the running app. On
a failing checkpoint it makes up to three bounded corrective attempts
(scoped fixes, or replanning the failing step and its downstream
plans), then escalates to you. At the end you get the full PR stack,
ready to review and merge in order.

## Ask about a repo

"What does HEAD look like vs main?", "is CI green on the PR?", "where
is the retry logic?" — the orchestrator answers these without spawning
an agent, through guaranteed read-only bridges (`cerebro git`, `gh`,
`grep`, `read`, `ls`) that allow-list every verb and flag. It also
checks `cerebro recall` — a literal search across all your past
sessions' transcripts and agent logs — before re-asking you something
you already answered in a prior session.

## Pair: watch and steer a live agent

Ask to *pair* (or *watch*, *steer*, *let me drive*) and the child runs
in pair mode (`execute`, `apply-review`, `doc-write`). Planning remains the
supervisor's responsibility; read-only reviews have no live steering:

* **Observe** — from a *second* cerebro session, say "observe
  \<session-id\>" (the id from the `PAIR MODE` banner; it names the
  orchestrator session, not a child). That session tails every live
  paired child at once and narrates in plain English what each one is
  doing — following the gist, flagging the decisions that matter (new
  abstractions, schema changes, security paths, public APIs) and
  quoting the shaping code. Observation only reads logs; it never
  disturbs the agents.
* **Steer** — `cerebro steer "<message>"` injects one instruction into
  the live child as its next turn and returns immediately. (Pass the
  pipe path from the banner first when several paired children run at
  once.) After each turn the child waits a short window
  (`CEREBRO_PAIR_IDLE`, default 60s) for steering; a quiet window lets
  it finish on its own. Steer is for small nudges.
* **Restart** — `cerebro restart "<diagnosis>"` is for when a paired
  execute child has gone fundamentally off (wrong assumptions, drifted
  from the spec) and steering its poisoned context is futile. It
  abandons the child and unconditionally tears down everything the run
  produced — the fresh branch, its PR, and the task's worktree are all
  deleted (your main checkout was never touched) — then hands the
  orchestrator the diagnosis so it can relaunch a fresh execute with a
  corrected prompt. Same arg shape as steer (pass the pipe path first
  when several run at once).

When the child finishes, steering is reported back with its source. Observer
and supervisor corrections restore the approved contract and do not change the
spec. An unambiguous requirement change from a direct user steering command is
folded into the spec and affected plans; a real ambiguity is returned to you.
The supervisor reports what changed.

## Drive it from your editor (ACP)

`cerebro acp` speaks the [Agent Client Protocol](https://agentclientprotocol.com),
so an ACP-aware editor such as Zed can drive the supervisor turn by turn.
Cerebro relays native protocol features and preserves the same guarded role;
available tools remain constrained by that role.

For each editor session the proxy:

1. Mints a durable Cerebro session and a Cerebro-owned project directory.
2. Spawns `opencode acp` or `claude-agent-acp` with the session environment.
3. Pins the guarded supervisor role: OpenCode's built-in mode plus shared skill
   instructions and session permissions, or a small Claude ACP agent wrapper
   around the same supervisor skill. No native OpenCode agent definition is used.
4. Relays JSON-RPC with session ID remapping and records the native conversation
   ID for subsequent load/resume.

Your repo is never written to from the ACP path: it is passed to the child as an
ACP `additional_directory`, and the child's session cwd is the cerebro-owned
project dir.

### Register it in Zed

Add a custom agent server to your Zed `settings.json`:

```jsonc
"agent_servers": {
  "Cerebro": { "type": "custom", "command": "cerebro", "args": ["acp"] }
}
```

Zed launches `cerebro acp` once (long-lived); each thread's cwd comes from the
editor. Open a Cerebro thread in a repo, send "draft a plan first" → "go", and
the `cerebro execute` child binds to the session (visible in `cerebro list`),
opens its PR, and reports back through the editor. Restart Zed and resume the
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

* **opencode (default):** uses native V2 `opencode acp` (minimum 2.0.19).
* **claude:** uses `@agentclientprotocol/claude-agent-acp` via `npx`, which
  needs **Node ≥ 22** + `npx` on PATH. Set `CEREBRO_BACKEND=claude` (and, for a
  gateway, `CEREBRO_CLAUDE_BASE_URL`).
* **codex:** supported in terminal sessions; it has no native ACP endpoint.

OpenCode 2.0.19's native ACP catalog may initially select a stock model before
a custom provider loads. Check the selected model before sending work when
using a custom provider.

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

ACP is **editor-driven and turn-by-turn**. The interactive **pair / observe /
watch-and-steer** features stay TUI-only (`cerebro` / `cerebro --observe`): the
ACP orchestrator is a sequence of per-turn upstream sessions, not a persistent
pairable process. `cerebro list` shows ACP-created sessions and `cerebro
--resume <id>` works on them like any other.

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

Python **≥ 3.10** plus the `mcp` package. `cerebro cerebro-mcp` prefers Homebrew's
`python3` (`/opt/homebrew/bin/python3`) — macOS system python3 is 3.9, too old —
and auto-installs the SDK to your user site on first run if it's missing:

```bash
brew install python
/opt/homebrew/bin/python3 -m pip install --user --break-system-packages mcp
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
jobs`. `cerebro wait <job-id>` blocks on completion notification without polling logs,
status files or child PIDs; `cerebro cancel <job-id>` deliberately stops the monitor and its
full descendant process tree.

If the child process itself is interrupted, its resumable conversation id was
persisted the instant it started. The orchestrator resumes each interrupted
child via `--resume` instead of redoing it (and instead of duplicating commits).
Stored ids stay resumable for `CEREBRO_CHILD_SESSION_TTL` seconds
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

## Skip the ceremony

Planning and review are the default, never skipped on the
orchestrator's own judgement. Say "just do it", "skip the plan", or
"fix it directly" and it uses the inline-prompt shortcuts instead —
straight to an editing child, no plan file or findings file in
between.

## Guardrails and autonomy

cerebro is built to be autonomous *within* your requirements, never
about them:

* **The session spec is the contract.** Before planning, the
  orchestrator records what you actually asked for; every requirement
  change updates it (prior versions are archived, never lost). It
  lives on disk, so it survives context compaction. During execution
  the orchestrator may adapt a plan that turns out wrong and keep
  going **as long as the adjusted work still satisfies the spec** — but
  anything that would (or even might) drop a requirement, change
  asked-for behaviour, or expand scope stops and asks you first. Doubt
  counts as divergence.
* **Plan-first by default.** Skipping the plan or the review requires
  you to ask for it explicitly.
* **The orchestrator cannot mutate anything.** Its tools are
  restricted by native backend permissions and an allow-listed Cerebro MCP
  command tool. Large bodies use the tool's `stdin` field; arguments never go
  through a shell. Mutations happen only in
  role-scoped children; the reviewer is sandboxed read-only.
* **Done means observed working.** End-to-end verification in the
  running app is a non-negotiable part of the definition of done.

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
  "model": "",
  "review_model": "",
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
| `backend` | `CEREBRO_BACKEND` | CLI for the supervisor and all children: `opencode`, `codex`, `claude` | `opencode` |
| `model` | `CEREBRO_MODEL` | native model ID for the supervisor and editing children | backend default |
| `review_model` | `CEREBRO_REVIEW_MODEL` | native model ID for review/audit/verify/improve on the same backend | `model`, or backend default |
| `claude_base_url` | `CEREBRO_CLAUDE_BASE_URL` | optional Anthropic-compatible endpoint for the claude backend (e.g. a local Ollama `/v1/messages` server, or any proxy). empty = the claude.ai subscription `claude` is logged into. when set, the effective model (`CEREBRO_MODEL`, or `CEREBRO_REVIEW_MODEL` for review/verification) must name a model the endpoint serves | empty (subscription) |
| `claude_auth_token` | `CEREBRO_CLAUDE_AUTH_TOKEN` | bearer token for the optional Claude gateway | empty (`ollama` placeholder for a local gateway) |
| `timeout` | `CEREBRO_TIMEOUT` | wall-clock cap (s) per child call | `0` (no cap, so e2e runs and CI waits are never killed) |
| `child_idle_timeout` | `CEREBRO_CHILD_IDLE_TIMEOUT` | optional parser inactivity bound (s); native transports own completion/stall handling | `0` (disabled) |
| `child_session_ttl` | `CEREBRO_CHILD_SESSION_TTL` | how long (s) a stored child id stays resumable | `86400` (24h) |
| `pair_idle` | `CEREBRO_PAIR_IDLE` | steering window (s) after each paired turn | `60` |
| `pair_stall` | `CEREBRO_PAIR_STALL` | native stream inactivity bound (s) when no tool is running | `180` |
| `pair_stall_busy` | `CEREBRO_PAIR_STALL_BUSY` | native stream inactivity bound (s) while a tool runs | `450` |
| `pair_stall_retries` | `CEREBRO_PAIR_STALL_RETRIES` | max restart attempts for a stalled paired child | `2` |
| `pair_stall_backoff` | `CEREBRO_PAIR_STALL_BACKOFF` | base (s) for the exponential restart backoff | `5` |
| `opencode_cmd` | `CEREBRO_OPENCODE_CMD` | opencode executable | `opencode` |
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
CEREBRO_MODEL=qwen2.5-coder:14b \
cerebro
```

Leave `CEREBRO_CLAUDE_BASE_URL` unset to keep using the subscription.
For an authenticated gateway, set `CEREBRO_CLAUDE_AUTH_TOKEN` to the
real key (the default is a placeholder local no-auth servers ignore).
Claude Code can't infer the context window for a model id it doesn't
recognize (anything not a built-in Claude alias) and falls back to 200k.
If your catalog entry for `CEREBRO_MODEL` declares a `contextTokens`
field (see "Model catalog" below), cerebro also exports
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>` into every spawned `claude` so
auto-compaction doesn't fire at the 200k default on a larger-window model.
Claude Code may still cap that value at its assumed window for the id (the
status line can read 200k); for direct `claude --model <id>` launches use
`cerebro model-env <id> [--no-compact]` to print the same exports, with
`--no-compact` as the escape hatch that forces the true window at the cost
of disabling compaction.

### Review and model selection

Review, audit, verification and improvement use the session's backend, with fresh
role-scoped context. `CEREBRO_REVIEW_MODEL` optionally selects another model on
that backend; it otherwise inherits `CEREBRO_MODEL`. Leaving both empty uses the
native backend's configured default. `--model` overrides the model for one
child command. Changing the model never changes the backend.

OpenCode needs native `provider/model` IDs. Codex and Claude accept their own
native IDs; Cerebro does not infer a backend from punctuation. Keep the model
catalog relevant to the selected backend, and choose a vision-capable model when
runtime verification needs screenshots. Resuming a session restores its recorded
backend, regardless of the current `CEREBRO_BACKEND` default.

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
  .agents/skills/                    # shared supervisor, observer and child skills
  .claude/skills/                    # links to the same skill files
  learnings.md                       # confirmed preferences read by the supervisor
  overlays/<target>.md               # user-owned prompt overlays (append onto shipped prompts)
  templates/AGENTS.md, CLAUDE.md     # defaults dropped into new repos (edit freely)
  worktrees/<ckey>/                  # isolated per-task execute worktrees
                                     #   (GC stale ones with `cerebro worktrees cleanup`)
  sessions/<id>/
    metadata.json                    # backend, role and native conversation ID
    detached-jobs/                   # job ownership, result and final status
    spec.md                          # current session spec (requirements of record)
    spec-history.jsonl               # every prior spec version
    plans/                           # plan markdown files
                                     #   (each <name>.md has a plain-English
                                     #    <name>-readable.md companion beside it)
    children/                        # native logs of every child + review findings
    audits/                          # independent plan-audit findings
    improvements/improve.md          # latest `cerebro improve` hill-climbing findings
    review-state/                    # per-repo last-reviewed SHA
```

The native session bindings and completion ownership are covered in
[ARCHITECTURE.md](ARCHITECTURE.md#durable-state-and-identity).
