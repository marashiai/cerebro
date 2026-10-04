// Package watch lets Jev observe one agent's event stream and decides when a
// predefined nudge should be sent back to the agent.
package watch

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/marashiai/cerebro/internal/agent"
	"github.com/marashiai/cerebro/internal/jev"
)

//go:embed questions.json
var questionsJSON []byte

//go:embed nudges.json
var nudgesJSON []byte

const (
	activityLimit = 4000 // a batch event keeps its diff or output
	historyText   = 300  // earlier events are summarized
	historyLimit  = 40
	batchSize     = 8
	stateLimit    = 60000 // characters of JSON sent to Jev
)

// Activity is one observed event as Jev sees it, with a stable ID it can cite.
type Activity struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
	Text string `json:"text"`
}

// Nudge is a predefined message for the agent.
type Nudge struct {
	Reason    string `json:"reason"`
	Evidence  string `json:"evidence"`
	Text      string `json:"text"`
	Interrupt bool   `json:"interrupt"`
}

// Config holds the run's fixed context and policy.
type Config struct {
	Client       *jev.Client
	Threshold    float64
	MaxNudges    int
	Request      string         // the user's task, verbatim
	Instructions []string       // repository and user instruction files, verbatim
	Repository   map[string]any // facts gathered by the watcher, e.g. git state and file inventory
	Log          func(record map[string]any)
}

type Watcher struct {
	cfg       Config
	questions map[string]jev.Question
	templates map[string]string
	Nudges    chan Nudge

	mu         sync.Mutex
	userInputs []string
	pending    []Activity
	history    []Activity
	files      map[string]string // path -> event that last changed it
	commands   []map[string]any
	started    time.Time
	nudges     []Nudge
	nudgedAt   map[string]int  // reason -> last event observed when its nudge was sent
	escalated  map[string]bool // reason -> its one interrupt has been used
	sequence   int
	busy       bool
	flush      bool
	wake       chan struct{}
	failed     bool
}

func New(cfg Config) (*Watcher, error) {
	w := &Watcher{cfg: cfg, Nudges: make(chan Nudge, 4), files: map[string]string{}, nudgedAt: map[string]int{}, escalated: map[string]bool{},
		wake: make(chan struct{}, 1), userInputs: []string{cfg.Request}, started: time.Now()}
	if err := json.Unmarshal(questionsJSON, &w.questions); err != nil {
		return nil, fmt.Errorf("questions.json: %w", err)
	}
	if err := json.Unmarshal(nudgesJSON, &w.templates); err != nil {
		return nil, fmt.Errorf("nudges.json: %w", err)
	}
	for reason := range w.questions["reason"].Criteria {
		if _, ok := w.templates[reason]; !ok && reason != "none" {
			return nil, fmt.Errorf("no nudge template for reason %q", reason)
		}
	}
	return w, nil
}

// Observe queues agent events for classification.
func (w *Watcher) Observe(events []agent.Event) {
	w.mu.Lock()
	defer w.mu.Unlock()
	for _, event := range events {
		text := describe(event)
		if text == "" {
			continue
		}
		w.sequence++
		id := fmt.Sprintf("e%d", w.sequence)
		for _, file := range event.Files {
			w.files[file] = id
		}
		if event.Command != "" {
			command := map[string]any{"event": id, "command": clip(event.Command, 300)}
			if event.Exit != nil {
				command["exit"] = *event.Exit
			}
			w.commands = append(w.commands, command)
		}
		w.pending = append(w.pending, Activity{ID: id, Kind: event.Kind, Text: clip(text, activityLimit)})
		if event.Kind == "turn_end" || event.Kind == "final" {
			w.flush = true
		}
	}
	w.signal()
}

// UserInput records a later user message; it supersedes earlier requests where they conflict.
func (w *Watcher) UserInput(text string) {
	w.mu.Lock()
	w.userInputs = append(w.userInputs, text)
	w.mu.Unlock()
}

// Idle reports that nothing is queued or being classified.
func (w *Watcher) Idle() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return len(w.Nudges) == 0 && (w.failed || (!w.busy && len(w.pending) == 0))
}

// Flush asks for classification of whatever is queued, without waiting for a full batch.
func (w *Watcher) Flush() {
	w.mu.Lock()
	w.flush = true
	w.mu.Unlock()
	w.signal()
}

func (w *Watcher) signal() {
	select {
	case w.wake <- struct{}{}:
	default:
	}
}

// Run classifies batches until ctx ends. Jev failures stop observation but
// never stop the agent.
func (w *Watcher) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-w.wake:
		case <-time.After(3 * time.Second):
			w.Flush()
			continue
		}
		w.mu.Lock()
		if w.failed || w.busy || len(w.pending) == 0 || (!w.flush && len(w.pending) < batchSize) {
			w.mu.Unlock()
			continue
		}
		count := min(batchSize, len(w.pending))
		events := append([]Activity(nil), w.pending[:count]...)
		w.pending = w.pending[count:]
		w.flush = len(w.pending) > 0 && w.flush
		w.busy = true
		state := w.state(events)
		w.mu.Unlock()

		nudge, err := w.classify(ctx, state, events)
		if err != nil && ctx.Err() == nil {
			// One retry absorbs a transient provider error before observation stops.
			nudge, err = w.classify(ctx, state, events)
		}
		if nudge != nil {
			w.Nudges <- *nudge // still busy, so the run cannot finish before the nudge is taken
		}

		w.mu.Lock()
		for _, event := range events {
			event.Text = clip(event.Text, historyText)
			w.history = append(w.history, event)
		}
		if len(w.history) > historyLimit {
			w.history = w.history[len(w.history)-historyLimit:]
		}
		w.busy = false
		if err != nil {
			w.failed = true
			w.cfg.Log(map[string]any{"type": "jev_failure", "error": err.Error()})
		}
		w.mu.Unlock()
		w.signal()
	}
}

func (w *Watcher) state(events []Activity) map[string]any {
	// Snapshot shared state: Jev serializes it while Observe keeps appending.
	commands := append([]map[string]any(nil), w.commands[max(0, len(w.commands)-20):]...)
	files := make(map[string]string, len(w.files))
	for file, id := range w.files {
		files[file] = id
	}
	state := map[string]any{
		"user_request":        w.userInputs[0],
		"later_user_messages": w.userInputs[1:],
		"standing_rules":      w.cfg.Instructions,
		"repository":          w.cfg.Repository,
		"progress": map[string]any{
			"files_changed":   files,
			"commands":        commands,
			"elapsed_seconds": int(time.Since(w.started).Seconds()),
		},
		"nudges_already_sent": append([]Nudge(nil), w.nudges...),
		"recent_history":      append([]Activity(nil), w.history...),
		"events":              events,
	}
	// Keep the request, rules and current events; shed older context first.
	for size(state) > stateLimit {
		history := state["recent_history"].([]Activity)
		if len(history) == 0 {
			break
		}
		state["recent_history"] = history[len(history)/2+1:]
	}
	if size(state) > stateLimit {
		repository := map[string]any{}
		for key, value := range w.cfg.Repository {
			if key != "files" {
				repository[key] = value
			}
		}
		state["repository"] = repository
	}
	return state
}

func size(value any) int {
	data, _ := json.Marshal(value)
	return len(data)
}

func (w *Watcher) classify(ctx context.Context, state map[string]any, events []Activity) (*Nudge, error) {
	questions := map[string]jev.Question{}
	for name, question := range w.questions {
		questions[name] = question
	}
	evidence := jev.Question{Type: "choice", Instructions: "Select the event that best shows the concern, or none.",
		Criteria: map[string]string{"none": "No event shows a concern"}}
	visible := append(append([]Activity(nil), state["recent_history"].([]Activity)...), events...)
	for _, event := range visible {
		if event.Kind != "turn_end" { // a turn boundary is context, not evidence
			evidence.Criteria[event.ID] = "Event " + event.ID
		}
	}
	questions["evidence"] = evidence
	call, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	response, raw, err := w.cfg.Client.Evaluate(call, state, questions)
	record := map[string]any{"type": "jev", "events": events, "raw": json.RawMessage(nonEmpty(raw))}
	if err != nil {
		record["error"] = err.Error()
		w.cfg.Log(record)
		return nil, err
	}
	attention, reason, cited := response.Answers["attention"], response.Answers["reason"], response.Answers["evidence"]
	record["classification"] = map[string]any{"attention": attention.Choice, "confidence": attention.Confidence,
		"reason": reason.Choice, "evidence": cited.Choice}
	defer w.cfg.Log(record)
	if attention.Choice != "concern" || attention.Confidence < w.cfg.Threshold || reason.Confidence < w.cfg.Threshold ||
		reason.Choice == "none" || cited.Choice == "none" {
		return nil, nil
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.nudges) >= w.cfg.MaxNudges {
		record["suppressed"] = "nudge limit"
		return nil, nil
	}
	var event Activity
	for _, candidate := range append(append([]Activity(nil), w.history...), events...) {
		if candidate.ID == cited.Choice {
			event = candidate
		}
	}
	// The first nudge for a concern steers. The same concern interrupts once,
	// and only when it cites an event newer than that nudge, so work the agent
	// did before reading the nudge, or explained after it, cannot escalate.
	previous, nudged := w.nudgedAt[reason.Choice]
	if nudged && (w.escalated[reason.Choice] || sequenceOf(event.ID) <= previous) {
		record["suppressed"] = "already nudged"
		return nil, nil
	}
	nudge := Nudge{Reason: reason.Choice, Evidence: event.Text, Interrupt: nudged,
		Text: render(w.templates[reason.Choice], event.Text, w.userInputs)}
	w.nudgedAt[reason.Choice] = w.sequence
	w.escalated[reason.Choice] = nudged
	w.nudges = append(w.nudges, nudge)
	record["nudge"] = nudge
	return &nudge, nil
}

func sequenceOf(id string) int {
	var number int
	fmt.Sscanf(id, "e%d", &number)
	return number
}

func render(template, evidence string, inputs []string) string {
	text := strings.ReplaceAll(template, "{evidence}", clip(evidence, 400))
	text = strings.ReplaceAll(text, "{latest}", clip(inputs[len(inputs)-1], 600))
	return "[cerebro] " + text
}

func describe(event agent.Event) string {
	switch event.Kind {
	case "message", "final":
		return event.Text
	case "command":
		text := "$ " + event.Command
		if event.Exit != nil {
			text += fmt.Sprintf("\n(exit %d)", *event.Exit)
		}
		if event.Output != "" {
			text += "\n" + event.Output
		}
		return text
	case "file_change":
		return "edited " + strings.Join(event.Files, ", ") + "\n" + event.Output
	case "tool":
		return "tool " + event.Text
	case "tool_result":
		status := "succeeded"
		if event.Exit != nil && *event.Exit != 0 {
			status = "failed"
		}
		return "tool " + status + ": " + event.Output
	case "turn_end":
		return "turn ended " + event.Text
	}
	return ""
}

func clip(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	return text[:limit] + "…"
}

func nonEmpty(raw []byte) []byte {
	if len(raw) == 0 {
		return []byte("null")
	}
	return raw
}
