package agent

import (
	"encoding/json"
	"fmt"
	"strings"
)

// pi drives Pi's RPC mode. Verified on Pi 0.99.2: a prompt sent with
// streamingBehavior "steer" during a run is queued and taken at the next model
// step; abort ends the run at once, and input sent before the abort is
// acknowledged is discarded with the aborted run.
type pi struct {
	send     func(any) error
	nextID   int
	requests map[string]piRequest
	pending  []string
	run      int
	active   bool
	starting bool
	settled  bool
	aborting bool
}

type piRequest struct {
	kind string
	run  int
}

func newPi(send func(any) error) *pi { return &pi{send: send, requests: map[string]piRequest{}} }

func (p *pi) request(kind string, fields map[string]any) error {
	p.nextID++
	id := fmt.Sprintf("cerebro-%d", p.nextID)
	p.requests[id] = piRequest{kind, p.run}
	message := map[string]any{"id": id, "type": kind}
	for key, value := range fields {
		message[key] = value
	}
	return p.send(message)
}

func (p *pi) start(prompt string) error { return p.steer(prompt) }

func (p *pi) steer(text string) error {
	p.settled = false
	p.pending = append(p.pending, text)
	return p.flush()
}

func (p *pi) interrupt(text string) error {
	p.settled = false
	p.pending = append(p.pending, text)
	if p.active && !p.aborting {
		p.aborting = true
		if err := p.request("abort", nil); err != nil {
			return err
		}
	}
	return p.flush()
}

func (p *pi) flush() error {
	if len(p.pending) == 0 || p.aborting {
		return nil
	}
	for _, request := range p.requests {
		if request.kind == "prompt" {
			return nil
		}
	}
	text := strings.Join(p.pending, "\n\n")
	p.pending = nil
	return p.request("prompt", map[string]any{"message": text, "streamingBehavior": "steer"})
}

func (p *pi) ended() bool {
	return p.settled && !p.starting && !p.active && len(p.pending) == 0 && len(p.requests) == 0
}

func (p *pi) handle(line []byte) ([]Event, error) {
	var event struct {
		Type    string          `json:"type"`
		ID      string          `json:"id"`
		Success bool            `json:"success"`
		Error   json.RawMessage `json:"error"`
		Data    struct {
			Disposition string `json:"disposition"`
		} `json:"data"`
		ToolCallID string `json:"toolCallId"`
		ToolName   string `json:"toolName"`
		Args       struct {
			Command string `json:"command"`
			Path    string `json:"path"`
		} `json:"args"`
		IsError bool `json:"isError"`
		Result  struct {
			Content []struct {
				Text string `json:"text"`
			} `json:"content"`
		} `json:"result"`
		Message struct {
			Role       string          `json:"role"`
			StopReason string          `json:"stopReason"`
			Content    json.RawMessage `json:"content"`
		} `json:"message"`
	}
	if err := json.Unmarshal(line, &event); err != nil {
		return nil, err
	}
	var raw struct {
		Args json.RawMessage `json:"args"`
	}
	json.Unmarshal(line, &raw)
	var events []Event
	switch event.Type {
	case "response":
		request := p.requests[event.ID]
		delete(p.requests, event.ID)
		if !event.Success {
			return nil, errorf("pi %s failed: %s", request.kind, string(event.Error))
		}
		switch request.kind {
		case "prompt":
			switch event.Data.Disposition {
			case "started":
				// A previous run's settlement may still be buffered; this ACK
				// precedes the new run's agent_start.
				if request.run == p.run {
					p.active, p.settled, p.starting = true, false, true
				}
			case "queued":
			default:
				return nil, errorf("pi prompt disposition %q", event.Data.Disposition)
			}
		case "abort":
			p.aborting = false
		}
	case "agent_start":
		p.run++
		p.starting, p.settled, p.active = false, false, true
	case "agent_settled":
		if !p.starting {
			p.active, p.settled = false, true
			events = append(events, Event{Kind: "turn_end"})
		}
	case "tool_execution_start":
		if event.Args.Command != "" {
			events = append(events, Event{Kind: "command", Command: event.Args.Command, Text: event.ToolName})
		} else if event.ToolName == "edit" || event.ToolName == "write" {
			events = append(events, Event{Kind: "file_change", Files: []string{event.Args.Path}, Text: event.ToolName,
				Output: tail(string(raw.Args), 4000)})
		} else {
			events = append(events, Event{Kind: "tool", Text: event.ToolName})
		}
	case "tool_execution_end":
		output := ""
		for _, part := range event.Result.Content {
			output += part.Text
		}
		events = append(events, Event{Kind: "tool_result", Output: tail(output, 2000),
			Exit: map[bool]*int{true: intPtr(1), false: intPtr(0)}[event.IsError]})
	case "message_end":
		if event.Message.Role == "assistant" {
			// User messages carry string content; assistant messages carry parts.
			var parts []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}
			json.Unmarshal(event.Message.Content, &parts)
			var texts []string
			for _, part := range parts {
				if part.Type == "text" && part.Text != "" {
					texts = append(texts, part.Text)
				}
			}
			if len(texts) > 0 {
				events = append(events, Event{Kind: "message", Text: strings.Join(texts, "\n")})
			}
		}
	}
	return events, p.flush()
}
