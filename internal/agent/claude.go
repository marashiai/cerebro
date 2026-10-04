package agent

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
)

// claude drives Claude Code's stream-json mode. Verified on 2.1.289: with
// --replay-user-messages each input is echoed, with its uuid, when it is taken
// into the conversation; input taken mid-turn joins that turn and shares its
// single result. An interrupt control request ends the turn with an error
// result, after which the next input runs as its own turn.
type claude struct {
	send     func(any) error
	sent     map[string]bool
	taken    map[string]bool
	turnOpen bool
	results  int
}

func newClaude(send func(any) error) *claude {
	return &claude{send: send, sent: map[string]bool{}, taken: map[string]bool{}}
}

func newUUID() string {
	var b [16]byte
	rand.Read(b[:])
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func (c *claude) start(prompt string) error { return c.steer(prompt) }

func (c *claude) steer(text string) error {
	id := newUUID()
	c.sent[id] = true
	return c.send(map[string]any{"type": "user", "uuid": id, "message": map[string]string{"role": "user", "content": text}})
}

func (c *claude) interrupt(text string) error {
	// The interrupt may discard input not yet taken; the new text supersedes it.
	for id := range c.sent {
		if !c.taken[id] {
			delete(c.sent, id)
		}
	}
	if c.turnOpen {
		request := map[string]any{"type": "control_request", "request_id": "cerebro-" + newUUID(),
			"request": map[string]string{"subtype": "interrupt"}}
		if err := c.send(request); err != nil {
			return err
		}
	}
	return c.steer(text)
}

func (c *claude) ended() bool {
	if c.results == 0 || c.turnOpen {
		return false
	}
	for id := range c.sent {
		if !c.taken[id] {
			return false
		}
	}
	return true
}

type claudeBlock struct {
	Type      string          `json:"type"`
	Text      string          `json:"text"`
	ID        string          `json:"id"`
	Name      string          `json:"name"`
	Input     json.RawMessage `json:"input"`
	ToolUseID string          `json:"tool_use_id"`
	Content   json.RawMessage `json:"content"`
	IsError   bool            `json:"is_error"`
}

func (c *claude) handle(line []byte) ([]Event, error) {
	var event struct {
		Type     string `json:"type"`
		Subtype  string `json:"subtype"`
		IsReplay bool   `json:"isReplay"`
		UUID     string `json:"uuid"`
		Result   string `json:"result"`
		Message  struct {
			Content json.RawMessage `json:"content"`
		} `json:"message"`
	}
	if err := json.Unmarshal(line, &event); err != nil {
		return nil, err
	}
	var blocks []claudeBlock
	json.Unmarshal(event.Message.Content, &blocks)
	var events []Event
	switch event.Type {
	case "user":
		if event.IsReplay && c.sent[event.UUID] {
			c.taken[event.UUID] = true
			c.turnOpen = true
		}
		for _, block := range blocks {
			if block.Type == "tool_result" {
				events = append(events, Event{Kind: "tool_result", Output: tail(contentText(block.Content), 2000),
					Exit: map[bool]*int{true: intPtr(1), false: intPtr(0)}[block.IsError]})
			}
		}
	case "assistant":
		c.turnOpen = true
		for _, block := range blocks {
			switch block.Type {
			case "text":
				events = append(events, Event{Kind: "message", Text: block.Text})
			case "tool_use":
				var input struct {
					Command  string `json:"command"`
					FilePath string `json:"file_path"`
				}
				json.Unmarshal(block.Input, &input)
				switch {
				case input.Command != "":
					events = append(events, Event{Kind: "command", Command: input.Command, Text: block.Name})
				case input.FilePath != "" && (block.Name == "Edit" || block.Name == "Write" || block.Name == "MultiEdit"):
					events = append(events, Event{Kind: "file_change", Files: []string{input.FilePath}, Text: block.Name,
						Output: tail(string(block.Input), 4000)})
				default:
					events = append(events, Event{Kind: "tool", Text: block.Name + " " + tail(string(block.Input), 300)})
				}
			}
		}
	case "result":
		c.results++
		c.turnOpen = false
		events = append(events, Event{Kind: "turn_end", Text: event.Subtype})
	}
	return events, nil
}

func contentText(raw json.RawMessage) string {
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return text
	}
	var blocks []claudeBlock
	json.Unmarshal(raw, &blocks)
	out := ""
	for _, block := range blocks {
		out += block.Text
	}
	return out
}
