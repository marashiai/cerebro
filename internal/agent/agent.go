// Package agent runs one native coding agent (Codex, Claude or Pi) headlessly
// and translates its event stream into a small common vocabulary.
package agent

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Event is one observable step of the agent's work.
type Event struct {
	Kind    string   `json:"kind"` // message, command, file_change, tool, turn_end, error
	Text    string   `json:"text,omitempty"`
	Command string   `json:"command,omitempty"`
	Output  string   `json:"output,omitempty"`
	Exit    *int     `json:"exit,omitempty"`
	Files   []string `json:"files,omitempty"`
}

// Options select the native backend and its model settings.
type Options struct {
	Backend    string
	Executable string
	Model      string
	Effort     string
	Dir        string
	Stderr     io.Writer
}

// protocol is one backend's view of the native stream.
type protocol interface {
	start(prompt string) error
	steer(text string) error
	interrupt(text string) error
	handle(line []byte) ([]Event, error)
	ended() bool
}

// Agent owns the native process. Lines arrive on Lines; handle them in the
// same goroutine that calls Steer and Interrupt.
type Agent struct {
	Lines  <-chan []byte
	Exited <-chan error
	cmd    *exec.Cmd
	proto  protocol
	stdin  io.WriteCloser
	mu     sync.Mutex
}

// Start launches the backend and submits the task prompt.
func Start(opts Options, prompt string) (*Agent, error) {
	executable := opts.Executable
	if executable == "" {
		executable = opts.Backend
	}
	var argv []string
	switch opts.Backend {
	case "codex":
		argv = []string{"app-server"}
	case "claude":
		argv = []string{"-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
			"--replay-user-messages", "--permission-mode", "bypassPermissions"}
		if opts.Model != "" {
			argv = append(argv, "--model", opts.Model)
		}
		if opts.Effort != "" {
			argv = append(argv, "--effort", opts.Effort)
		}
	case "pi":
		argv = []string{"--mode", "rpc"}
		if opts.Model != "" {
			argv = append(argv, "--model", opts.Model)
		}
		if opts.Effort != "" {
			argv = append(argv, "--thinking", opts.Effort)
		}
	default:
		return nil, fmt.Errorf("unknown backend %q (use codex, claude or pi)", opts.Backend)
	}
	cmd := exec.Command(executable, argv...)
	cmd.Dir = opts.Dir
	cmd.Stderr = opts.Stderr
	// The agent gets the user's environment minus Jev's credentials, so a
	// watched run and a bare run see the same environment.
	for _, entry := range os.Environ() {
		if !strings.HasPrefix(entry, "JEV_") {
			cmd.Env = append(cmd.Env, entry)
		}
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	a := &Agent{cmd: cmd, stdin: stdin}
	send := func(message any) error {
		data, err := json.Marshal(message)
		if err != nil {
			return err
		}
		a.mu.Lock()
		defer a.mu.Unlock()
		_, err = a.stdin.Write(append(data, '\n'))
		return err
	}
	switch opts.Backend {
	case "codex":
		a.proto = newCodex(send, opts)
	case "claude":
		a.proto = newClaude(send)
	case "pi":
		a.proto = newPi(send)
	}
	lines := make(chan []byte)
	exited := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1<<20), 64<<20)
		for scanner.Scan() {
			lines <- append([]byte(nil), scanner.Bytes()...)
		}
		if scanner.Err() != nil {
			// An unreadable stream would leave the agent blocked on its stdout.
			syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		}
		close(lines)
		exited <- cmd.Wait()
	}()
	a.Lines, a.Exited = lines, exited
	if err := a.proto.start(prompt); err != nil {
		a.Stop()
		return nil, err
	}
	return a, nil
}

// Handle interprets one native output line.
func (a *Agent) Handle(line []byte) ([]Event, error) { return a.proto.handle(line) }

// Ended reports that the native agent finished its work and holds no queued input.
func (a *Agent) Ended() bool { return a.proto.ended() }

// Steer delivers text to the running turn at its next model step, or starts a turn.
func (a *Agent) Steer(text string) error { return a.proto.steer(text) }

// Interrupt stops the running turn; text starts the next turn and supersedes unread input.
func (a *Agent) Interrupt(text string) error { return a.proto.interrupt(text) }

// Stop ends the native process group.
func (a *Agent) Stop() {
	a.mu.Lock()
	a.stdin.Close()
	a.mu.Unlock()
	if a.cmd.Process == nil {
		return
	}
	syscall.Kill(-a.cmd.Process.Pid, syscall.SIGTERM)
	done := make(chan struct{})
	go func() {
		for range a.Lines {
		}
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		syscall.Kill(-a.cmd.Process.Pid, syscall.SIGKILL)
	}
}

func intPtr(v int) *int { return &v }

func errorf(format string, args ...any) error { return fmt.Errorf(format, args...) }
