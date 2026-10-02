# cerebro

**Talk to one agent; get focused changes, fresh reviews and verified results.**

![cerebro demo](docs/demo.gif)

The animation was recorded with an earlier workflow.

`cerebro` turns your chosen backend — **Pi, Codex, or Claude Code** —
into a supervisor. You describe the work; it records the requirements, delegates
implementation to children in an appropriate checkout, and keeps a short, adjustable
plan of possible commits. Fresh review and appropriate verification accompany
the work. The supervisor uses guarded Cerebro
commands and never edits the repo itself. Each session and all its children
stay on one backend, using that backend's native conversations and tools.

Cerebro keeps its role and lifecycle instructions small and reuses the upstream
`engineering`, `supervise` and `hashimoto-review` skills. Optional Jev watching
classifies native child events cheaply and wakes the waiting supervisor only
for possible drift or uncertainty. The supervisor decides how to respond.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/install.sh | bash
cerebro
```

Name a repo by path and describe the change. Authorize commits, pushes or PRs
when you want them; a plan does not need separate approval. Requires your selected
CLI (`pi`, `codex`, or `claude`), `jq`, and `python3` (plus `git`/`gh` for PR work;
`rg` recommended). Pi requires version **0.99.2 or later** and **Node ≥ 22.19.0**.
Install the official [Pi CLI](https://pi.dev/) with:

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.99.2
CEREBRO_BACKEND=pi cerebro
CEREBRO_BACKEND=codex cerebro
CEREBRO_BACKEND=claude cerebro
```

Pi is the library default; existing backend/model configuration takes precedence.
Cerebro manages Pi's small MCP/role extension and shared skills; no additional
MCP, subagent or permission extensions are required.

## What you get

* **Adjustable commit plans** — a short list of possible commits, revised as
  facts and your instructions change within the agreed scope.
* **Fresh review after each commit** — independent review explains the design,
  risks and failure paths with Hunk notes or file-anchored notes inline. Human
  review is offered while authorized work continues.
* **Verification through the real boundary** — `engineering` guides checks
  appropriate to the change, including runtime and browser evidence when needed.
* **Unattended work when requested** — invoke `supervise` for the workflow phase
  you want handled, with genuine dependencies, scoped delegation and delivery
  authority kept explicit.
* **Cheap scope watching** — enable `--watch` per development task, or use
  `jev_enabled=1`. In-scope events stay silent; the supervisor receives cited
  notices and retains control over steering or authorized abandonment.
* **Nothing lost** — sessions resume; interrupted children continue
  where they stopped; blocked children pause with a question instead
  of guessing.
* **It learns you** — durable preferences carried into every future
  session, across repos, plus local prompt overlays to tune any prompt
  surface without forking.
* **It improves itself** — `cerebro improve` mines its own accumulated
  traces for problems that recur across runs and proposes the smallest
  fixes back into the harness. Applying proposals follows your scope and
  authorization; the reviewer only proposes them.

How to drive each of these: **[docs/USAGE.md](docs/USAGE.md)**.
How it works inside — design, decisions, constraints:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Use cases

Example ways people drive cerebro day to day. Mostly you talk to the
orchestrator in plain English (shown as an example prompt) and it runs
the machinery; a few are CLI commands you run yourself (shown as a
command). Each block is collapsible — click to expand.

<details>
<summary><strong>Ship a feature (the core loop)</strong></summary>

Describe the outcome and constraints. The supervisor delegates focused changes,
updates the short plan as needed, and reviews each authorized commit with fresh
context. Commit, push, PR and merge actions follow your authorization and the
repository's instructions.

> Example prompt: "In ~/code/api, add rate limiting to the login endpoint. Make small commits and explain each change in review."

</details>

<details>
<summary><strong>Make a small change</strong></summary>

Small tasks use the same lightweight workflow. A formal plan file and a separate
readable companion are unnecessary.

> Example prompt: "Just fix the typo in the footer copyright year in ~/code/site, no plan needed."

</details>

<details>
<summary><strong>Ask about a repo (read-only)</strong></summary>

Questions are answered through the guaranteed read-only bridges, no
agent spawned.

> Example prompt: "Is CI green on the open PR in ~/code/api, and where does the retry logic live?"

</details>

<details>
<summary><strong>Run unattended with supervise</strong></summary>

Explicitly invoke `supervise` to delegate an unattended workflow. It applies
within the phase and scope you requested, without routine confirmation gates.
Pushes, PRs and merges still need authority; the skill does not grant it.

> Example prompt: "Use supervise to implement and verify the agreed migration in ~/code/api unattended. You may commit and open PRs; leave merges to me."

</details>

<details>
<summary><strong>Watch scope with Jev</strong></summary>

Set a private `CEREBRO_JEV_API_KEY` or `jev_api_key` in your Cerebro config, then
ask the supervisor to watch a development task. `--watch` is available for
`execute`, `apply-review` and `doc-write`; `--no-watch` disables it for a task.
Watching defaults off unless `jev_enabled=1`.

Jev classifies event batches against the current spec, task and adjustable plans.
A significant or uncertain result returns a notice with the running job ID,
steering pipe, sequence and original cited event. The supervisor can steer or
continue waiting with `wait <job-id> --after <sequence>`. It assesses notices
against the current requirements; an older stored warning can be dismissed and
acknowledged. Jev makes no steering or restart decision. Restarting an execute
task retires its native conversation and retains its checkout, files, branch
and PR. Run the corrected task in that retained checkout;
follow-up tasks use steering or cancellation. Destructive cleanup is separate.

Watched tasks use MCP or a detached completion socket. Classification failures
stop the child explicitly while preserving resumable work. See the
[Jev guide](docs/USAGE.md#watch-development-scope-with-jev) for configuration and
notice handling.

> Example prompt: "Build the CSV export in ~/code/api with Jev watching. Correct drift within the agreed scope; ask me before abandoning the task."

</details>

<details>
<summary><strong>Resume a session and continue interrupted work</strong></summary>

Sessions are durable. Resume by id and say continue to pick up
interrupted in-flight children where they stopped.

```bash
cerebro --resume <session-id>
```

> Example prompt: "continue where we left off"

</details>

<details>
<summary><strong>Answer a paused child's question</strong></summary>

A blocked child pauses with a question as its closing message instead
of guessing. Answer it and it resumes exactly where it stopped.

> Example prompt: "Use Postgres, not SQLite — go ahead with that."

</details>

<details>
<summary><strong>Teach it your preferences</strong></summary>

Reveal a general preference and it is recorded, consolidated into
learnings, and carried into every future session across repos.

> Example prompt: "From now on, always keep diffs small and don't add backwards-compat shims unless I ask."

</details>

## Uninstall

```bash
cerebro-uninstall            # removes the symlink + PATH block
cerebro-uninstall --purge    # also deletes the clone; session state is never touched
```

## Development

```bash
bash tests/run.sh
```

The optional native checks use installed CLIs with deterministic providers on
loopback, isolated configuration, and guards against unconfigured CLI launches:

```bash
python3 tests/native_runtime.py
python3 tests/native_runtime.py --parents
python3 tests/native_runtime.py --pair --background
```

Append backend names to select a subset. The checks exercise native delegation,
worktree isolation, session resume, guarded review, and paired steering. These
deterministic checks cover CLI and process boundaries; they do not verify live
model behavior or establish classification accuracy. Diagnostic artifacts remain
under `/tmp`. The Claude ACP proxy and PTY MCP tests additionally require their
optional SDKs and Python 3.10 or later.

Conventions live in [AGENTS.md](AGENTS.md); the demo GIF is rendered
from [docs/demo/demo.tape](docs/demo/demo.tape) with
[vhs](https://github.com/charmbracelet/vhs).
