// Command cerebro runs one native coding agent while Jev watches its event
// stream and nudges it back toward what the user asked.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/marashiai/cerebro/internal/agent"
	"github.com/marashiai/cerebro/internal/jev"
	"github.com/marashiai/cerebro/internal/watch"
)

const usage = `usage: cerebro run [flags] "task" | -

Runs a Codex, Claude or Pi agent on the task. Jev watches the agent's events
and nudges it when it drifts from the request. Lines typed on stdin while it
runs are sent to the agent as follow-up messages. The final answer goes to
stdout; progress goes to stderr; everything is logged as JSONL.

Jev settings: JEV_API_KEY, JEV_MODEL (default jev-latest), JEV_ENDPOINT.
`

func main() {
	if len(os.Args) < 2 || os.Args[1] != "run" {
		fmt.Fprint(os.Stderr, usage)
		os.Exit(2)
	}
	os.Exit(run(os.Args[2:]))
}

func run(args []string) int {
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	backend := flags.String("backend", "codex", "native agent: codex, claude or pi")
	executable := flags.String("executable", "", "path to the native CLI (default: the backend name)")
	model := flags.String("model", "", "model for the agent (default: the backend's)")
	effort := flags.String("effort", "", "reasoning effort for the agent (default: the backend's)")
	dir := flags.String("dir", ".", "working directory for the agent")
	noJev := flags.Bool("no-jev", false, "run the bare agent without Jev")
	threshold := flags.Float64("threshold", 0.8, "minimum Jev confidence for a nudge")
	maxNudges := flags.Int("max-nudges", 3, "maximum nudges per run")
	timeout := flags.Duration("timeout", 0, "stop the run after this long (0: no limit)")
	logPath := flags.String("log", "", "JSONL log path (default: ~/.cerebro/runs/<time>.jsonl)")
	rules := flags.String("rules", "", "file of personal standing rules for Jev (default: none)")
	flags.Usage = func() { fmt.Fprint(os.Stderr, usage); flags.PrintDefaults() }
	if err := flags.Parse(args); err != nil || flags.NArg() != 1 {
		flags.Usage()
		return 2
	}
	task := flags.Arg(0)
	readFollowUps := true
	if task == "-" {
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			return fail(err)
		}
		task, readFollowUps = string(data), false
	}
	workdir, err := filepath.Abs(*dir)
	if err != nil {
		return fail(err)
	}
	logger, err := openLog(*logPath)
	if err != nil {
		return fail(err)
	}
	defer logger.close()
	logger.write(map[string]any{"type": "start", "backend": *backend, "model": *model, "effort": *effort,
		"dir": workdir, "jev": !*noJev, "task": task})

	// A signal ends the run through the deferred Stop, which ends the agent's
	// process group; the agent runs in its own group and would otherwise survive.
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	stopped := ctx
	if *timeout > 0 {
		ctx, cancel = context.WithTimeout(ctx, *timeout)
		defer cancel()
	}

	var watcher *watch.Watcher
	if !*noJev {
		client, err := jev.New(os.Getenv("JEV_API_KEY"), envOr("JEV_MODEL", "jev-latest"), os.Getenv("JEV_ENDPOINT"))
		if err != nil {
			return fail(fmt.Errorf("%w (set JEV_API_KEY, or pass --no-jev)", err))
		}
		standing, err := instructions(workdir, *rules)
		if err != nil {
			return fail(err)
		}
		watcher, err = watch.New(watch.Config{Client: client, Threshold: *threshold, MaxNudges: *maxNudges,
			Request: task, Instructions: standing, Repository: repository(workdir), Log: logger.write})
		if err != nil {
			return fail(err)
		}
		go watcher.Run(ctx)
	}

	stderr := logger.stderrFile()
	ag, err := agent.Start(agent.Options{Backend: *backend, Executable: *executable, Model: *model,
		Effort: *effort, Dir: workdir, Stderr: stderr}, task)
	if err != nil {
		return fail(err)
	}
	defer ag.Stop()

	followUps := make(chan string)
	if readFollowUps {
		go func() {
			scanner := bufio.NewScanner(os.Stdin)
			for scanner.Scan() {
				if text := strings.TrimSpace(scanner.Text()); text != "" {
					followUps <- text
				}
			}
		}()
	}
	var nudges <-chan watch.Nudge
	if watcher != nil {
		nudges = watcher.Nudges
	}

	lines := ag.Lines
	final := ""
	idleCheck := time.NewTicker(250 * time.Millisecond)
	defer idleCheck.Stop()
	for {
		select {
		case <-ctx.Done():
			if stopped.Err() != nil {
				logger.write(map[string]any{"type": "end", "status": "signal"})
				return 130
			}
			logger.write(map[string]any{"type": "end", "status": "timeout"})
			fmt.Fprintln(os.Stderr, "cerebro: run timed out")
			return 124
		case line, ok := <-lines:
			if !ok {
				lines = nil
				continue
			}
			logger.write(map[string]any{"type": "native", "line": json.RawMessage(validJSON(line))})
			events, err := ag.Handle(line)
			if err != nil {
				logger.write(map[string]any{"type": "end", "status": "error", "error": err.Error()})
				return fail(err)
			}
			for _, event := range events {
				logger.write(map[string]any{"type": "event", "event": event})
				progress(event)
				if event.Kind == "message" || event.Kind == "final" {
					final = event.Text
				}
			}
			if watcher != nil {
				watcher.Observe(events)
				if ag.Ended() {
					watcher.Flush()
				}
			}
		case text := <-followUps:
			logger.write(map[string]any{"type": "user", "text": text})
			if watcher != nil {
				watcher.UserInput(text)
			}
			if err := ag.Steer(text); err != nil {
				return fail(err)
			}
		case nudge := <-nudges:
			logger.write(map[string]any{"type": "nudge", "nudge": nudge})
			fmt.Fprintf(os.Stderr, "\n[jev %s] %s\n", nudge.Reason, nudge.Text)
			deliver := ag.Steer
			if nudge.Interrupt {
				deliver = ag.Interrupt
			}
			if err := deliver(nudge.Text); err != nil {
				return fail(err)
			}
		case err := <-ag.Exited:
			if !ag.Ended() {
				if err == nil {
					err = errors.New("native agent exited before finishing")
				}
				logger.write(map[string]any{"type": "end", "status": "error", "error": err.Error()})
				return fail(err)
			}
		case <-idleCheck.C:
		}
		if ag.Ended() && (watcher == nil || watcher.Idle()) {
			logger.write(map[string]any{"type": "end", "status": "completed", "final": final})
			fmt.Println(final)
			return 0
		}
	}
}

// instructions returns the repository instruction files the agent itself reads,
// plus the user's personal rules, so Jev judges the agent against them.
func instructions(dir, rules string) ([]string, error) {
	var found []string
	for _, name := range []string{"AGENTS.md", "CLAUDE.md"} {
		if data, err := os.ReadFile(filepath.Join(dir, name)); err == nil {
			found = append(found, name+":\n"+string(data))
		}
	}
	if rules != "" {
		data, err := os.ReadFile(rules)
		if err != nil {
			return nil, err
		}
		found = append(found, "Personal rules of the user:\n"+string(data))
	}
	return found, nil
}

// repository gathers git facts that decided past corrections: branch state,
// commit identity against recent authors, and what already exists.
func repository(dir string) map[string]any {
	git := func(args ...string) []string {
		out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).Output()
		if err != nil {
			return nil
		}
		var lines []string
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			if line != "" {
				lines = append(lines, line)
			}
		}
		return lines
	}
	branch := git("rev-parse", "--abbrev-ref", "HEAD")
	if branch == nil {
		return map[string]any{"git": false}
	}
	limit := func(items []string, n int) []string {
		if len(items) > n {
			return append(items[:n], fmt.Sprintf("... %d more", len(items)-n))
		}
		return items
	}
	return map[string]any{
		"branch":         branch[0],
		"branches":       limit(git("branch", "--format=%(refname:short)"), 20),
		"git_identity":   strings.Join(append(git("config", "user.name"), git("config", "user.email")...), " "),
		"recent_authors": limit(git("log", "-5", "--format=%an <%ae>"), 5),
		"files":          limit(git("ls-files"), 400),
	}
}

func progress(event agent.Event) {
	switch event.Kind {
	case "command":
		fmt.Fprintf(os.Stderr, "$ %s\n", firstLine(event.Command))
	case "file_change":
		fmt.Fprintf(os.Stderr, "edited %s\n", strings.Join(event.Files, ", "))
	case "message":
		fmt.Fprintf(os.Stderr, "> %s\n", firstLine(event.Text))
	}
}

func firstLine(text string) string {
	text, _, _ = strings.Cut(text, "\n")
	if len(text) > 160 {
		text = text[:160] + "…"
	}
	return text
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func validJSON(line []byte) []byte {
	if json.Valid(line) {
		return line
	}
	quoted, _ := json.Marshal(string(line))
	return quoted
}

func fail(err error) int {
	fmt.Fprintln(os.Stderr, "cerebro:", err)
	return 1
}

type runLog struct {
	mu     sync.Mutex
	file   *os.File
	stderr *os.File
}

func openLog(path string) (*runLog, error) {
	if path == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return nil, err
		}
		path = filepath.Join(home, ".cerebro", "runs", time.Now().UTC().Format("20060102T150405.000000")+".jsonl")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return nil, err
	}
	stderr, err := os.OpenFile(strings.TrimSuffix(path, ".jsonl")+".stderr.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		file.Close()
		return nil, err
	}
	fmt.Fprintln(os.Stderr, "cerebro: logging to", path)
	return &runLog{file: file, stderr: stderr}, nil
}

func (l *runLog) write(record map[string]any) {
	record["time"] = time.Now().UTC().Format(time.RFC3339Nano)
	data, err := json.Marshal(record)
	if err != nil {
		data, _ = json.Marshal(map[string]any{"type": "log_error", "error": err.Error()})
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.file.Write(append(data, '\n'))
}

func (l *runLog) stderrFile() *os.File { return l.stderr }

func (l *runLog) close() {
	l.file.Close()
	l.stderr.Close()
}
