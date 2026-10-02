# Agent Instructions

**The user's direct instructions always take precedence over these
instructions.**

Use the [engineering skill](lib/payloads/skills/engineering/SKILL.md) for
development. Use [supervise](lib/payloads/skills/supervise/SKILL.md) for an
explicit unattended handoff. Ordinary sessions keep a short adjustable plan
of possible commits and offer [Hashimoto diff notes](lib/payloads/skills/hashimoto-review/SKILL.md)
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

`cerebro` is a Bash CLI split into small sourced modules. `bin/cerebro`
is a thin entry point: it resolves its own path (through any PATH
symlink), sources the library, then calls `main`. Nothing else lives in
the entry point.

```
bin/cerebro            # entry point: locate lib, source modules, dispatch
lib/config.sh          # shell options + CEREBRO_* env defaults (sourced first)
lib/helpers.sh         # say/warn/die, exit-code helpers, path + repo resolution, usage
lib/payloads.sh        # shared skill/prompt and configuration payload loaders
lib/payloads/          # skills, prompts, native session-binding hooks/plugins
lib/session-store.sh   # session metadata (incl. backend) + child-agent session store
lib/backend.sh         # backend dispatch (child_run/launch/resume/materialise)
lib/backend-pi.sh       # Pi: native TUI + RPC child events and steering
lib/backend-codex.sh   # Codex: native TUI + app-server thread/turn API
lib/backend-claude.sh  # Claude: native TUI + stream-json children
lib/python/            # guarded MCP commands, native transports, stream parsing,
                       #   detached completion, Jev scope watching and path helpers
lib/pair.sh            # pair-programming mode shared helpers (watch + steer a live child)
lib/commands/*.sh      # one file per subcommand group (plan, execute, review, ...)
lib/main.sh            # dispatch table mapping argv[0] to a cmd_* function
tests/run.sh           # plain-bash test suite for the read-only bridges + child paths
```

Keep modules cohesive: a new subcommand goes in `lib/commands/`, gets a
`cmd_<name>` function, and a route in `lib/main.sh`. Only `config.sh`
runs ordering-sensitive top-level code (it sets shell options); every
other module is function and string definitions, so load order among
them does not matter. Non-shell content stays out of shell strings:
multi-line python belongs in `lib/python/` (invoked as `python3
"$CEREBRO_LIB_DIR/python/<name>.py"`; shell one-liners are fine
inline), and prompts/templates/config payloads belong in
`lib/payloads/`. Run `bash tests/run.sh` before proposing changes.

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
