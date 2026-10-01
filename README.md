# cerebro

**Talk to one agent; get planned, reviewed, verified pull requests.**

![cerebro demo](docs/demo.gif)

`cerebro` turns your chosen backend — **OpenCode V2, Codex, or Claude Code** —
into a supervisor. You describe the work; it records the requirements, delegates
implementation to children in isolated worktrees, and gates delivery on
independent review and real verification. The supervisor uses guarded Cerebro
commands and never edits the repo itself. Each session and all its children
stay on one backend, using that backend's native conversations and tools.

Shared skills define the roles. A separate observer can watch live work and,
when you authorize it, steer drift back to the approved spec.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/install.sh | bash
cerebro
```

Name a repo by path, describe the change, read the plan it drafts, say
"go". Requires your selected CLI (`opencode` **2.0.19 or later within V2**,
`codex`, or `claude`), `jq`, and `python3` (plus `git`/`gh` for PR work;
`rg` recommended). OpenCode V1 is not supported.

```bash
CEREBRO_BACKEND=opencode cerebro   # default
CEREBRO_BACKEND=codex cerebro
CEREBRO_BACKEND=claude cerebro
```

## What you get

* **Planned, reviewed PRs** — plan → your "go" → branch, PR, independent
  review loop, fixes applied, docs updated.
* **Verified, not just green** — done means the change was observed
  working in the running app (Playwright or with you), never unit
  tests alone.
* **Big changes as stacked PRs** — large specs become an ordered plan
  suite, one shippable PR per step, checkpoint-gated.
* **Watch & steer live agents** — pair with a running agent, observe
  it narrated from another session, inject course corrections.
* **Nothing lost** — sessions resume; interrupted children continue
  where they stopped; blocked children pause with a question instead
  of guessing.
* **It learns you** — durable preferences carried into every future
  session, across repos, plus local prompt overlays to tune any prompt
  surface without forking.
* **It improves itself** — `cerebro improve` mines its own accumulated
  traces for problems that recur across runs and proposes the smallest
  fixes back into the harness. A human must approve every overlay or source
  change; the reviewer never applies proposals itself.

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

Name a repo, describe the change, read the plan it drafts, and say go.
You get a branch, a PR, an independent review loop with fixes applied, and
end-to-end verification in the running app.

> Example prompt: "In ~/code/api, add rate limiting to the login endpoint — draft a plan first."

</details>

<details>
<summary><strong>Skip the ceremony (inline edit)</strong></summary>

Say it plainly — "just do it", "skip the plan" — and it goes straight
to an editing child, with no plan or findings file in between.

> Example prompt: "Just fix the typo in the footer copyright year in ~/code/site, no plan needed."

</details>

<details>
<summary><strong>Ask about a repo (read-only)</strong></summary>

Questions are answered through the guaranteed read-only bridges, no
agent spawned.

> Example prompt: "Is CI green on the open PR in ~/code/api, and where does the retry logic live?"

</details>

<details>
<summary><strong>Break a large task into stacked PRs</strong></summary>

Hand it a spec too big for one PR and it decomposes the work into an
ordered suite of plans — one shippable PR each. You approve the
decomposition once, then it executes the stack checkpoint-gated.

> Example prompt: "Migrate ~/code/api from REST to gRPC — this is big, break it into a stack of PRs."

</details>

<details>
<summary><strong>Pair, observe, and steer a live agent</strong></summary>

Run a child in pair mode, watch it from a second session, and nudge it
mid-run.

> Example prompt (session A): "Build the CSV export in ~/code/api and let me pair with it."

Then from a second terminal run:

```bash
cerebro --observe <session-id>
```

Tell that session "observe it", and to nudge: "tell it to stream the
file instead of buffering".

</details>

<details>
<summary><strong>Auto-steer and auto-restart while you're away</strong></summary>

Pre-authorize the observing session to act on its own: it steers a
drifting agent back and restarts one that has gone fundamentally
off-spec.

> Example prompt (to the observing session): "Watch my other session while I'm out — steer it back if it drifts from the spec, and restart it if it goes fundamentally off the rails."

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
python3 tests/native_runtime.py --acp opencode
```

Append backend names to select a subset. The checks exercise native delegation,
worktree isolation, session resume, guarded review, and paired steering. Native
background completion is covered for OpenCode and Codex; Claude uses a
foreground command. These checks prove the CLI and process boundaries, not
model decision quality. Diagnostic artifacts remain under `/tmp`.
The ACP check exercises OpenCode's native launcher; the Python ACP proxy and
PTY MCP tests additionally require their optional SDKs and Python 3.10 or later.

Conventions live in [AGENTS.md](AGENTS.md); the demo GIF is rendered
from [docs/demo/demo.tape](docs/demo/demo.tape) with
[vhs](https://github.com/charmbracelet/vhs).
