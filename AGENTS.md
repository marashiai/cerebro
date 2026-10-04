# Agent Instructions

**The user's direct instructions always take precedence over these
instructions.**

Use the [engineering skill](skills/engineering/SKILL.md) for
development. Use [supervise](skills/supervise/SKILL.md) for an
explicit unattended handoff. Ordinary sessions keep a short adjustable plan
of possible commits and offer [Hashimoto diff notes](skills/hashimoto-review/SKILL.md)
after each authorized commit, without requiring review acceptance to continue.

## Branches

Use Angular-style Conventional Commits prefixes for branch names:
`feat/`, `fix/`, `chore/`, `refactor/`, `docs/`, `test/`, `perf/`,
`style/`, `build/`, `ci/`, `revert/`. The portion after the prefix is
kebab-case and stays short.

Examples: `feat/oauth-login`, `fix/null-pointer-on-resume`,
`refactor/extract-payments-service`, `chore/bump-deps`.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/)
format: `<type>(<optional-scope>): <subject>`. The subject line must
be **80 characters or fewer**.

No AI attribution: commits use the locally configured git user.
Never add co-author tags, agent names, or any other AI-attribution
metadata to commit messages or trailers.

## Project layout

`cerebro` is a small Go command. It runs one native coding agent (Codex, Claude
or Pi) headlessly while Jev, a System 1 classifier, watches the agent's event
stream and sends predefined nudges when the agent drifts from the request.

```
cmd/cerebro/            # CLI: flags, run loop, follow-up input, JSONL log, repository facts
internal/agent/         # native processes and per-backend protocols (steer, interrupt, completion)
internal/jev/           # Jev HTTP client and strict answer validation
internal/watch/         # batching, Jev context, wake policy, nudges
internal/watch/*.json   # Jev questions and nudge templates (embedded)
evals/                  # paired bare vs Jev harness, frozen task fixtures, published results
skills/                 # development workflow skills referenced above
```

The agent gets no instructions beyond the user's task; Jev's nudges are the
only addition. Keep it that way: behavior belongs in the nudge policy and
templates, not in agent prompts. Run `go vet ./... && go test -race ./...` and
`python3 -m unittest discover -s evals` before proposing changes.

## Rules (apply to every repository)

- **Never commit unless the user explicitly asks for it.** Leave
  changes staged or unstaged for the user to inspect.
- **Never make database or infrastructure changes unless the user
  explicitly asks.** This covers migrations, schema changes,
  Infrastructure-as-Code (Terraform / CloudFormation / Pulumi / Helm /
  Kubernetes manifests), CI workflow changes, and deployment
  configuration.
- **Never backfill, add a fallback mechanism, or introduce
  backwards-compatibility shims unless the user explicitly asks.** When
  changing behavior, replace the old path outright rather than keeping
  it alongside the new one. Do not add compatibility layers, legacy
  aliases, deprecation wrappers, version-conditional branches, or silent
  default fallbacks on your own initiative.
