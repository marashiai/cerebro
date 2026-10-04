# Cerebro

Cerebro runs one coding agent (Codex, Claude Code or Pi) on your task, exactly as
the agent would run on its own, while **Jev** watches what it does. Jev is a
cheap System 1 classifier: it reads the agent's live event stream (messages,
commands with exit codes, file diffs) against your request and your standing
rules. When a specific event shows the agent drifting, Jev sends one short
predefined nudge. Examples: claiming something is verified without evidence,
doing more than you asked, building something that already exists, working on
the wrong target, or adding needless process.

Each nudge ends with an escape hatch ("if this is intended, say why in one line
and continue"), so a false alarm costs one short turn. A concern that returns
after a nudge interrupts the agent once. Nudges are capped per run. With no
nudges, Cerebro is the bare agent.

```sh
go build -o cerebro ./cmd/cerebro
export JEV_API_KEY=...          # optional: JEV_MODEL, JEV_ENDPOINT
./cerebro run --backend codex --model gpt-6-luna "Fix the parser's quoted-comma handling"
./cerebro run --no-jev ...      # the bare agent, for comparison
```

While it runs, lines you type are sent to the agent as follow-up messages, and
Jev treats them as your latest word. Progress goes to stderr, the final answer to
stdout, and everything (native events, Jev classifications, nudges) to a JSONL
log under `~/.cerebro/runs/`. `--rules FILE` adds personal standing rules
for Jev. The repository's `AGENTS.md` and `CLAUDE.md` are always included.

Whether this helps is an open question, measured in [evals](evals/README.md):
the same agent with and without Jev, on frozen tasks, with a decision rule
fixed in advance.

The nudge categories come from 103 real corrections a user made to Codex and
Claude Code agents. Earlier experiments with a supervisor, implementor and
reviewer pipeline are recorded in [evals/results](evals/results); they showed no
advantage over a bare strong agent at roughly twice the time and cost.
