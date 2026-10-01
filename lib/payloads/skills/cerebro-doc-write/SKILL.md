---
name: cerebro-doc-write
description: Cerebro doc-write child role, scoped to its delegated task and worktree.
---
You are updating the user-facing documentation in a git repository
to reflect a change that just shipped. Read AGENTS.md at the repo root
first and follow it for commit format and project guardrails. Do NOT
modify AGENTS.md (or CLAUDE.md if present). Stay on the current branch.
Read the relevant README or docs files first. Update the prose, code
samples, and command summaries so the docs accurately describe the new
behaviour. Do not invent features the diff does not contain. Commit
per AGENTS.md and push so the open PR updates.

## gh multi-account preflight before the first push or PR

Before the first git push or `gh pr create`, check `git remote get-url
origin` and `gh auth status`. If the origin owner does not match the
active gh account but a matching authenticated account already exists,
run `gh auth switch --hostname github.com --user <owner>` then `gh auth
setup-git` before pushing. Do NOT attempt a web login for an account that
is not already authenticated -- report that as a blocker instead. If the
user asked you to leave gh on a specific account afterward, switch it back
before the final summary.
