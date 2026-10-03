# Offline checks

```bash
bash tests/run.sh
```

The suite uses real Git repositories and deterministic native CLI transport
fixtures for Pi, Codex, and Claude. It exercises the production controller,
Bash runners, adapters, stream parser, durable monitors, MCP protocol, native
model/effort routing, and session bindings. It never calls a model provider or
installs dependencies. Existing ACP and PTY MCP SDK integration checks run when
an installed interpreter supplies their optional SDKs.

Task checks cover automatic independent review, terminal questions/incomplete
handoffs, malformed output, retry/restart, controller interruption before and
after a stage receipt, pending answers, duplicate-live-task prevention, quiet
commands, original evidence, omitted/empty/opaque model settings, and Claude
custom-endpoint native defaults. Workspace tests preserve dirty files and existing
branch history, isolate work only when requested, and refuse recreation of a
missing or replaced recorded checkout. Durable MCP tests cover parent disconnect
and cancellation of one task without affecting another.

Jev checks use a loopback classifier and deterministic evidence; they do not
establish the quality of a real model's judgment. Offline fixture results also
do not establish provider login or installed-native CLI integration. Live
provider comparisons belong to the separately authorized evaluation workflow.
