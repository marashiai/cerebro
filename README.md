# cerebro

**A strong supervisor, a focused implementor, and an independent review.**

Cerebro runs through your existing **Pi, Codex, or Claude Code** installation.
The supervisor inspects the repository and makes a concrete plan. One task packet
starts an implementor that does the coding and tests, then automatically starts
an independent reviewer. The supervisor adjudicates the implementation evidence,
original findings, and optional Jev assessment, and either finishes or delegates a
focused correction.

All roles keep the backend's normal tools, authentication, configuration, and
repository instructions. Separate role contexts and controller-owned orchestration
provide cooperation. Cerebro does not add a tool security sandbox, mandatory
verifier/documentation agents, review ceremonies, or learning framework.

## Start

```bash
curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/install.sh | bash
cerebro
```

Describe the goal and repository path. Commits, pushes, PRs, and deployment follow
your instructions and the repository's delivery authority.

Cerebro requires the selected native CLI, `jq`, `python3`, and Git for coding tasks.
Pi is the default backend and requires version 0.99.2 or newer. Choose another
backend with `CEREBRO_BACKEND=codex` or `CEREBRO_BACKEND=claude`.

## Model settings

Set role defaults in `$CEREBRO_HOME/config.json` (`~/.cerebro/config.json` by default):

```json
{
  "backend": "claude",
  "supervisor_model": "your-native-supervisor-model",
  "model": "your-native-implementor-model",
  "review_model": "your-native-review-model"
}
```

Optional `supervisor_effort`, `implementor_effort`, and `review_effort` are passed
unchanged to the selected backend. Environment variables override config, and a
task packet can override implementor/reviewer settings. Omitted model and effort
settings preserve native defaults. Prefer a different configured reviewer model;
Cerebro reports the resolved choices, preserves same-model selections, and does
not claim diversity when native defaults are unresolved.

## Watch and recover

Optional Jev watching reads native events and wakes the supervisor for concerns.
In-scope progress stays quiet. The supervisor can record a concise disposition,
steer a correction, or cancel within the task's authority. Completion and notices
use blocking event notifications, with no polling loop.

Tasks, native conversation IDs, original handoffs, and logs survive disconnects.
Questions and incomplete work stop before review. Resuming continues only the
unfinished stage; completed implementation/review stages do not run again.
Native work remains steerable with Jev disabled.

See [usage](docs/USAGE.md), [architecture](docs/ARCHITECTURE.md), and
[offline checks](tests/README.md). [Evaluation artifacts](evals/README.md) document
measured runs and their model/backend settings; older results describe the
workflow that existed when they were collected.
