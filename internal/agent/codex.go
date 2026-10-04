package agent

import (
	"encoding/json"
	"strings"
)

// codex drives `codex app-server` (JSON-RPC over stdio). Verified on
// codex-cli 0.160.0: turn/steer reaches an active turn at its next model step,
// turn/interrupt completes the turn as "interrupted" at once, and the
// interrupted command never reports completion.
type codex struct {
	send         func(any) error
	opts         Options
	nextID       int
	requests     map[int]string
	thread       string
	pending      []string
	active       map[string]bool
	completed    map[string]bool
	unsteerable  map[string]bool
	steering     string
	steeringTurn string
	interrupting string
}

func newCodex(send func(any) error, opts Options) *codex {
	return &codex{send: send, opts: opts, requests: map[int]string{}, active: map[string]bool{},
		completed: map[string]bool{}, unsteerable: map[string]bool{}}
}

func (c *codex) request(method string, params any, purpose string) error {
	c.nextID++
	c.requests[c.nextID] = purpose
	return c.send(map[string]any{"jsonrpc": "2.0", "id": c.nextID, "method": method, "params": params})
}

func (c *codex) inFlight(purposes ...string) bool {
	for _, purpose := range c.requests {
		for _, wanted := range purposes {
			if purpose == wanted {
				return true
			}
		}
	}
	return false
}

func (c *codex) start(prompt string) error {
	c.pending = []string{prompt}
	return c.request("initialize", map[string]any{"clientInfo": map[string]string{"name": "cerebro", "version": "3"}}, "initialize")
}

func (c *codex) steer(text string) error {
	c.pending = append(c.pending, text)
	return c.flush()
}

func (c *codex) interrupt(text string) error {
	c.pending = append(c.pending, text)
	if c.interrupting == "" {
		for turn := range c.active {
			c.interrupting = turn
			if err := c.request("turn/interrupt", map[string]string{"threadId": c.thread, "turnId": turn}, "interrupt"); err != nil {
				return err
			}
			break
		}
	}
	return c.flush()
}

// flush admits pending input one request at a time, so each input lands in a known turn.
func (c *codex) flush() error {
	if c.thread == "" || len(c.pending) == 0 || c.inFlight("turn", "steer") || c.active[c.interrupting] {
		return nil
	}
	var steerable string
	for turn := range c.active {
		if !c.unsteerable[turn] {
			steerable = turn
		}
	}
	if len(c.active) > 0 && steerable == "" {
		return nil
	}
	text := strings.Join(c.pending, "\n\n")
	c.pending = nil
	input := []map[string]string{{"type": "text", "text": text}}
	if steerable != "" {
		c.steering, c.steeringTurn = text, steerable
		return c.request("turn/steer", map[string]any{"threadId": c.thread, "expectedTurnId": steerable, "input": input}, "steer")
	}
	params := map[string]any{"threadId": c.thread, "input": input}
	if c.opts.Effort != "" {
		params["effort"] = c.opts.Effort
	}
	return c.request("turn/start", params, "turn")
}

func (c *codex) ended() bool {
	return len(c.completed) > 0 && len(c.active) == 0 && len(c.pending) == 0 && !c.inFlight("turn", "steer")
}

type codexItem struct {
	ID               string `json:"id"`
	Type             string `json:"type"`
	Text             string `json:"text"`
	Command          string `json:"command"`
	AggregatedOutput string `json:"aggregatedOutput"`
	ExitCode         *int   `json:"exitCode"`
	Tool             string `json:"tool"`
	Changes          []struct {
		Path string `json:"path"`
		Diff string `json:"diff"`
	} `json:"changes"`
}

func (c *codex) handle(line []byte) ([]Event, error) {
	var message struct {
		ID     *int            `json:"id"`
		Method string          `json:"method"`
		Params json.RawMessage `json:"params"`
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(line, &message); err != nil {
		return nil, err
	}
	if message.ID != nil && message.Method == "" {
		return nil, c.response(*message.ID, message.Result, message.Error)
	}
	if message.ID != nil {
		// Never-approve/full-access children should not ask; refuse rather than authorize.
		return nil, c.send(map[string]any{"jsonrpc": "2.0", "id": *message.ID,
			"error": map[string]any{"code": -32601, "message": "Unexpected host request"}})
	}
	var params struct {
		Turn struct {
			ID     string          `json:"id"`
			Status string          `json:"status"`
			Error  json.RawMessage `json:"error"`
		} `json:"turn"`
		Item codexItem `json:"item"`
	}
	json.Unmarshal(message.Params, &params)
	var events []Event
	switch message.Method {
	case "turn/started":
		c.active[params.Turn.ID] = true
	case "item/completed":
		item := params.Item
		switch item.Type {
		case "agentMessage":
			events = append(events, Event{Kind: "message", Text: item.Text})
		case "commandExecution":
			events = append(events, Event{Kind: "command", Command: item.Command, Output: tail(item.AggregatedOutput, 2000), Exit: item.ExitCode})
		case "fileChange":
			event := Event{Kind: "file_change"}
			for _, change := range item.Changes {
				event.Files = append(event.Files, change.Path)
				event.Output += change.Diff
			}
			event.Output = tail(event.Output, 4000)
			events = append(events, event)
		case "mcpToolCall":
			events = append(events, Event{Kind: "tool", Text: item.Tool})
		}
	case "turn/completed":
		turn := params.Turn
		requested := turn.ID == c.interrupting
		if requested {
			c.interrupting = ""
		}
		if turn.Status != "completed" && !(turn.Status == "interrupted" && requested) {
			return nil, errorf("codex turn %s: %s", turn.Status, string(turn.Error))
		}
		c.completed[turn.ID] = true
		delete(c.active, turn.ID)
		events = append(events, Event{Kind: "turn_end", Text: turn.Status})
	}
	return events, c.flush()
}

func (c *codex) response(id int, result, failure json.RawMessage) error {
	purpose := c.requests[id]
	delete(c.requests, id)
	if len(failure) > 0 && string(failure) != "null" {
		switch purpose {
		case "steer":
			// The turn is ending; the input starts the next turn instead.
			c.unsteerable[c.steeringTurn] = true
			c.pending = append([]string{c.steering}, c.pending...)
			return c.flush()
		case "interrupt":
			c.interrupting = ""
			return c.flush()
		}
		return errorf("codex %s failed: %s", purpose, string(failure))
	}
	var body struct {
		Thread struct {
			ID string `json:"id"`
		} `json:"thread"`
		Turn struct {
			ID string `json:"id"`
		} `json:"turn"`
	}
	json.Unmarshal(result, &body)
	switch purpose {
	case "initialize":
		if err := c.send(map[string]any{"jsonrpc": "2.0", "method": "initialized", "params": map[string]any{}}); err != nil {
			return err
		}
		params := map[string]any{"cwd": c.opts.Dir, "approvalPolicy": "never", "sandbox": "danger-full-access"}
		if c.opts.Model != "" {
			params["model"] = c.opts.Model
		}
		return c.request("thread/start", params, "thread")
	case "thread":
		c.thread = body.Thread.ID
	case "turn":
		if !c.completed[body.Turn.ID] {
			c.active[body.Turn.ID] = true
		}
	}
	return c.flush()
}

func tail(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	return "..." + text[len(text)-limit:]
}
