---
name: cerebro-commands
description: Literal command syntax, delegation, role models, guarded inspection and durable child completion.
---
# Cerebro commands

Use the Cerebro MCP `command` tool with an argv array excluding `cerebro`.
Arguments are literal: use absolute paths, no shell expansion or redirects.
Large bodies go in `stdin` with a command's `--stdin` flag:

```json
{"argv":["plan","--out","work","--stdin"],"stdin":"1. First possible commit\n2. Next possible commit"}
```

Workflow policy comes from `engineering`, `supervise` and `hashimoto-review`.
Load them with `guide <name>`. Commands do not grant delivery permissions.

## Requirements and working plan

```text
spec [show | history]
spec set <text> | spec set --stdin
plan <markdown> [--out <name>]
plan --stdin [--out <name>]
plan --from-file <absolute-path> [--out <name>]
plans [rm <name>]
```

`spec` records the current requirements and archives revisions. `plan` records
one short adjustable plan; repeating `--out` replaces that same file. In
ordinary sessions describe possible cohesive commits, preserve completed steps
and update the remaining work as facts or instructions change. Show it in chat
and proceed within the user's authority. `supervise` owns unattended workflow.

## Delegation

```text
execute <repo> (<task-file> | --prompt <task>)
        [--base <branch>] [--branch <new-branch>] [--pair] [--watch|--no-watch] [--model <id>]
apply-review <worktree> (<findings-file> [--notes <context>] | --prompt <task>)
             [--pair] [--watch|--no-watch] [--model <id>]
doc-write <worktree> (<task-file> [--notes <context>] | --prompt <task>)
          [--pair] [--watch|--no-watch] [--model <id>]
review <worktree> [--base <ref>] [--criteria-file <path>] [--explain] [--model <id>]
verify <worktree> (--plan <path> | --prompt <requirements>)
       [--context <context>] [--model <id>]
audit <repo> <plan-path> [--context <context>] [--out <name>] [--model <id>]
answer <child-session-id> <answer> [--model <id>]
```

`execute` creates an isolated task worktree and announces its path. Subsequent
commits, corrections, documentation and verification use that worktree.
`--base` pins the source/PR base; `--branch` pins the fresh branch. They must
differ. Include the authorized delivery actions in every task packet.

Children return terminal handoffs. A question is incomplete work: answer from
the contract when possible or relay the material decision, then use `answer`
to resume the same native conversation. Load `cerebro-child-flow` for recovery.

`review` uses fresh read-only context and returns a findings path. By default,
a re-review uses the previously reviewed ancestor on the same branch; `--base`
sets the exact comparison explicitly. In ordinary sessions use the previous
commit as the base and add `--explain` for Hashimoto teaching notes alongside
findings. After each commit relay its report and offer optional human review;
continuation does not require acceptance. Uncommitted authorized work can also
be reviewed. `--criteria-file` adds per-criterion static verdicts ending in
`ACCEPTANCE CRITERIA: MET` or `NOT MET`; unavailable runtime checks are EXTERNAL.

`apply-review` without findings or a prompt uses the last findings for this
repo/branch. Use `--prompt` for a scoped subsequent commit or correction. A
findings path returned by `review` must be used verbatim. `verify` retains
runtime/browser tools and returns `VERIFY: PASS`, `FAIL` or `BLOCKED`; read-only
review cannot prove runtime behavior. `audit` is optional, returning
`PLAN AUDIT: VIABLE` or `ISSUES FOUND` when plan inspection is requested.

## Automatic drift detection and steering

```text
steer [<pipe>] <message>
restart [<pipe>] <diagnosis>
```

Use `--watch` on development tasks to classify fresh native events with Jev;
`--no-watch` overrides `jev_enabled=1`. This requires a configured Jev key,
recorded spec and durable job. Normal progress is silent; a typed concern wakes
the existing parent with original evidence and confidence. Load `cerebro-pair`.
The parent assesses the concern, steers within its authority, and calls
`wait <job-id> --after <sequence>`. Classifications never grant permissions.
Use `--pair` for manual steering. Restart of execute work additionally requires
authority to abandon and remove the task's worktree/branch/PR; use steer or
cancel for follow-up children.

## Completion and recovery

```text
status
jobs
wait <job-id|absolute-output.status> [--after <notice-sequence>]
cancel <job-id>
detach --output <absolute-path> -- <child-command> [arguments...]
worktrees [cleanup]
```

Long commands detach automatically and the MCP call waits on a completion
notification, returning the final handoff, exit code, job ID and output path.
Watched jobs can first return `state: running`, `sequence` and `notice`.
They are still active; handle the notice and wait again before treating the
task as complete. Unacknowledged notices survive parent disconnects.
Closing the parent or timing out a tool does not cancel its children. On resume
inspect `status`/`jobs` and wait for live work instead of starting duplicates.
Only use `cancel`, `restart` or worktree cleanup within the granted authority.

## Inspection and diff notes

```text
read <repo> <path> [--range N:M] [--strict-missing]
read <absolute-file> [--range N:M] [--strict-missing]
grep <repo-or-absolute-dir> <pattern> [--glob G] [--type T]
     [--fixed-strings] [-i] [--path SUB] [--strict-missing]
ls <repo-or-absolute-dir> [path] [--strict-missing]
git <repo> <subcommand> [arguments...]
gh <repo> <subcommand> [arguments...]
hunk <hunk-arguments...>
recall <query>
```

File bridges confine repo-relative paths. The git/gh bridges reject mutating
verbs and unsafe flags; use their errors for supported shapes. They do not run
a shell. `gh` is unavailable to read-only reviewers.

`hunk` allows `skill path hunk-review` and safe `session` inspection, navigation
and additive `comment` calls. Use an absolute `--repo` or an exact session ID;
list sessions first and read Hunk's bundled skill. Supply comment batches with
`--stdin` and MCP stdin. Interactive `diff`/`show`, reload, removal and clearing
are unavailable. The user opens the diff window; reviews continue without one,
returning every teaching note with a code excerpt and file/line anchor.

## Models and preferences

```text
models [--json]
model-env <id> [--no-compact]
learnings
learn-note <observation>
learn-set <text> | learn-set --stdin
overlay show [<target>]
overlay set <target> <text>
overlay rm <target>
improve <cerebro-source-repo> [--context <focus>] [--meta] [--model <id>]
```

The optional `models-config.json` catalog provides native IDs, capabilities,
descriptions and context windows. `CEREBRO_SUPERVISOR_MODEL` selects the parent;
`CEREBRO_MODEL` selects implementation; `CEREBRO_REVIEW_MODEL` selects
review/audit/verify/improve, inheriting the implementation selection when unset.
An empty selection uses the native default; `--model` overrides one child call.
Pi accepts its native model selectors. Model punctuation never selects a backend.

Learnings store durable preferences. Overlays tune `system`, `execute`,
`apply-review`, `doc-write`, `grader` and `meta-*` surfaces. Improvement is an
explicit separate task; load `cerebro-improve` rather than rewriting the harness
during ordinary delivery. Supervisor commands expose this full surface;
reviewers can only
load guides, inspect files/git and write Hunk sidecar notes.
