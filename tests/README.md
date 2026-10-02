# Cerebro tests

Plain-bash test runner for cerebro's read-only bridge subcommands
(`cerebro git`, `cerebro gh`, `cerebro read`, `cerebro grep`,
`cerebro ls`). No external test framework.

```bash
bash tests/run.sh
```

Each test prints `PASS` / `FAIL` (or `SKIP` when `rg` is missing). The
runner exits non-zero if any assertion fails. The sandbox lives in
`$(mktemp -d)` and is cleaned up on exit.

Native Pi checks use the installed CLI and SDK against a scripted local model
provider. They create isolated Cerebro/native homes, repositories and Git remotes;
user settings and credentials remain untouched. Artifacts are retained under the
printed `/tmp/cerebro-real-backends-*` directory:

```bash
python3 tests/native_runtime.py --pair --background pi
CEREBRO_TEST_PYTHON=/path/to/python-with-mcp2 python3 tests/pi_runtime_test.py
python3 tests/native_runtime.py --watch pi
```

The feature driver exercises development, review follow-ups, documentation,
runtime verification, audit, fast/meta improvement, incomplete-child recovery,
restart ownership, stalls, timeout, detached completion/cancellation, parent
resume and the PTY MCP frontend. `CEREBRO_TEST_PYTHON` supplies the optional MCP
2 SDK, including configured servers with direct, default CodeMode and deferred
exposure. The watch run additionally uses the configured live Jev key against
synthetic scope drift. These tests verify native integration and controls;
scripted replies do not establish a real model's judgment or provider login.

For the official Playwright MCP journey, also set `CEREBRO_TEST_PLAYWRIGHT_MCP`
to its executable and `CEREBRO_TEST_CHROMIUM` to an installed Chromium binary.
The driver navigates a local page, clicks its button, reads the real accessibility
snapshot, captures a screenshot and checks image delivery to Pi's model request.
It also checks all three watched task roles, uncertainty handoff and classifier
failure using a loopback Jev-compatible classifier.

The `gh` validation tests do not require `gh` to be installed -- they
exercise validation paths that fire before any real `gh` invocation.

The interactive-guard tests (190-197) drive `cerebro` under an allocated
pseudo-terminal via `pty_run.py`, the way a controller such as Codex
(launched with `tty: true`) drives it: a genuine PTY with a non-shell
parent is accepted, pipes/redirects/no-TTY are rejected, and a full
interactive session can receive a prompt, continue, and end on EOF or
Ctrl-C through the PTY. They use only the python3 `pty`/`termios` stdlib
modules plus a stub `pi`, so no real backend is launched.
