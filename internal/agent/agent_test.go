package agent

import (
	"encoding/json"
	"strings"
	"testing"
)

type sink struct{ sent []map[string]any }

func (s *sink) send(message any) error {
	data, _ := json.Marshal(message)
	var decoded map[string]any
	json.Unmarshal(data, &decoded)
	s.sent = append(s.sent, decoded)
	return nil
}

func (s *sink) last() map[string]any { return s.sent[len(s.sent)-1] }

func line(value any) []byte { data, _ := json.Marshal(value); return data }

func mustEnded(t *testing.T, want bool, ended bool, message string) {
	t.Helper()
	if ended != want {
		t.Fatalf("%s: ended=%v, want %v", message, ended, want)
	}
}

// --- Codex ---

func codexSetup(t *testing.T) (*codex, *sink) {
	s := &sink{}
	c := newCodex(s.send, Options{Dir: "/tmp/x", Effort: "low"})
	c.start("task")
	reply := func(result any) {
		id := int(s.last()["id"].(float64))
		if _, err := c.handle(line(map[string]any{"id": id, "result": result})); err != nil {
			t.Fatal(err)
		}
	}
	reply(map[string]any{})
	reply(map[string]any{"thread": map[string]string{"id": "thread"}})
	if s.last()["method"] != "turn/start" {
		t.Fatalf("expected turn/start, got %v", s.last())
	}
	return c, s
}

func codexReply(t *testing.T, c *codex, request map[string]any, result any) {
	t.Helper()
	if _, err := c.handle(line(map[string]any{"id": int(request["id"].(float64)), "result": result})); err != nil {
		t.Fatal(err)
	}
}

func codexNotify(t *testing.T, c *codex, method string, params any) error {
	_, err := c.handle(line(map[string]any{"method": method, "params": params}))
	return err
}

func turn(id, status string) map[string]any {
	return map[string]any{"turn": map[string]string{"id": id, "status": status}}
}

func TestCodexSteersActiveTurnAndStartsIdleTurn(t *testing.T) {
	c, s := codexSetup(t)
	codexReply(t, c, s.last(), map[string]any{"turn": map[string]string{"id": "first"}})
	c.steer("[cerebro] fix it")
	steer := s.last()
	params := steer["params"].(map[string]any)
	if steer["method"] != "turn/steer" || params["expectedTurnId"] != "first" {
		t.Fatalf("active turn was not steered: %v", steer)
	}
	codexNotify(t, c, "turn/completed", turn("first", "completed"))
	mustEnded(t, false, c.ended(), "completion before the steer acknowledgement")
	codexReply(t, c, steer, map[string]string{"turnId": "first"})
	mustEnded(t, true, c.ended(), "acknowledged steer and completed turn")
	c.steer("[user] continue")
	if s.last()["method"] != "turn/start" {
		t.Fatalf("idle input did not start a turn: %v", s.last())
	}
}

func TestCodexRejectedSteerStartsNextTurn(t *testing.T) {
	c, s := codexSetup(t)
	codexReply(t, c, s.last(), map[string]any{"turn": map[string]string{"id": "first"}})
	c.steer("late")
	steer := s.last()
	if _, err := c.handle(line(map[string]any{"id": int(steer["id"].(float64)), "error": map[string]any{"code": -32600}})); err != nil {
		t.Fatal(err)
	}
	if s.last()["id"] != steer["id"] {
		t.Fatalf("rejected input was retried against the ending turn: %v", s.last())
	}
	codexNotify(t, c, "turn/completed", turn("first", "completed"))
	start := s.last()
	if start["method"] != "turn/start" || !strings.Contains(string(line(start)), "late") {
		t.Fatalf("rejected input did not start the next turn: %v", start)
	}
}

func TestCodexInterruptStartsNextTurnAndUnrequestedInterruptFails(t *testing.T) {
	c, s := codexSetup(t)
	codexReply(t, c, s.last(), map[string]any{"turn": map[string]string{"id": "first"}})
	c.steer("unread")
	steer := s.last()
	c.interrupt("urgent")
	stop := s.last()
	if stop["method"] != "turn/interrupt" {
		t.Fatalf("expected turn/interrupt, got %v", stop)
	}
	codexReply(t, c, steer, map[string]string{"turnId": "first"})
	if err := codexNotify(t, c, "turn/completed", turn("first", "interrupted")); err != nil {
		t.Fatal(err)
	}
	start := s.last()
	if start["method"] != "turn/start" || !strings.Contains(string(line(start)), "urgent") || strings.Contains(string(line(start)), "unread") {
		t.Fatalf("interrupt did not start the next turn with only its text: %v", start)
	}
	codexReply(t, c, start, map[string]any{"turn": map[string]string{"id": "second"}})
	codexNotify(t, c, "turn/completed", turn("second", "completed"))
	mustEnded(t, true, c.ended(), "an unanswered interrupt must not hold the run open")

	c, s = codexSetup(t)
	codexReply(t, c, s.last(), map[string]any{"turn": map[string]string{"id": "first"}})
	if err := codexNotify(t, c, "turn/completed", turn("first", "interrupted")); err == nil {
		t.Fatal("an unrequested interruption was accepted")
	}
}

func TestCodexRefusesHostRequestsAndNormalizesItems(t *testing.T) {
	c, s := codexSetup(t)
	codexReply(t, c, s.last(), map[string]any{"turn": map[string]string{"id": "first"}})
	c.handle(line(map[string]any{"id": 99, "method": "item/commandExecution/requestApproval", "params": map[string]any{}}))
	if s.last()["error"] == nil {
		t.Fatal("host request was not refused")
	}
	events, _ := c.handle(line(map[string]any{"method": "item/completed", "params": map[string]any{"item": map[string]any{
		"id": "c", "type": "commandExecution", "command": "go test ./...", "aggregatedOutput": "ok", "exitCode": 0}}}))
	if len(events) != 1 || events[0].Kind != "command" || events[0].Command != "go test ./..." || *events[0].Exit != 0 {
		t.Fatalf("command not normalized: %+v", events)
	}
}

// --- Claude ---

func claudeTaken(s *sink, text string) []byte {
	for i := len(s.sent) - 1; i >= 0; i-- {
		message, _ := s.sent[i]["message"].(map[string]any)
		if message != nil && message["content"] == text {
			echo := map[string]any{}
			for key, value := range s.sent[i] {
				echo[key] = value
			}
			echo["isReplay"] = true
			return line(echo)
		}
	}
	panic("not sent: " + text)
}

var claudeResult = line(map[string]any{"type": "result", "subtype": "success", "result": "done"})

func TestClaudeMidTurnInputSharesOneResult(t *testing.T) {
	s := &sink{}
	c := newClaude(s.send)
	c.start("task")
	c.handle(claudeTaken(s, "task"))
	c.steer("steer")
	c.handle(claudeTaken(s, "steer"))
	c.handle(claudeResult)
	mustEnded(t, true, c.ended(), "mid-turn steer joined the running turn")
}

func TestClaudeInputAfterResultNeedsItsOwnTurn(t *testing.T) {
	s := &sink{}
	c := newClaude(s.send)
	c.start("task")
	c.handle(claudeTaken(s, "task"))
	c.steer("late")
	c.handle(claudeResult)
	mustEnded(t, false, c.ended(), "pending input not yet taken")
	c.handle(claudeTaken(s, "late"))
	c.handle(claudeResult)
	mustEnded(t, true, c.ended(), "second turn finished")
	c.handle(line(map[string]any{"type": "user", "isReplay": true, "uuid": "foreign"}))
	mustEnded(t, true, c.ended(), "foreign replay ignored")
}

func TestClaudeInterruptSupersedesUnreadInput(t *testing.T) {
	s := &sink{}
	c := newClaude(s.send)
	c.start("task")
	c.handle(claudeTaken(s, "task"))
	c.steer("unread")
	c.interrupt("urgent")
	if s.sent[len(s.sent)-2]["type"] != "control_request" {
		t.Fatalf("no interrupt control request: %v", s.sent)
	}
	c.handle(line(map[string]any{"type": "result", "subtype": "error_during_execution"}))
	c.handle(claudeTaken(s, "urgent"))
	c.handle(claudeResult)
	mustEnded(t, true, c.ended(), "discarded unread input must not hold the run")
	c.interrupt("idle")
	if s.sent[len(s.sent)-2]["type"] == "control_request" {
		t.Fatal("idle agent was interrupted")
	}
}

// --- Pi ---

func piReply(t *testing.T, p *pi, request map[string]any, disposition string) {
	t.Helper()
	if _, err := p.handle(line(map[string]any{"type": "response", "id": request["id"], "success": true,
		"data": map[string]string{"disposition": disposition}})); err != nil {
		t.Fatal(err)
	}
}

func TestPiSettlesOnlyAfterItsOwnRun(t *testing.T) {
	s := &sink{}
	p := newPi(s.send)
	p.start("task")
	first := s.last()
	p.handle(line(map[string]string{"type": "agent_start"}))
	p.steer("steer")
	p.handle(line(map[string]string{"type": "agent_settled"}))
	mustEnded(t, false, p.ended(), "steer still waiting for admission")
	piReply(t, p, first, "started")
	p.handle(line(map[string]string{"type": "agent_settled"}))
	second := s.last()
	if second["type"] != "prompt" || second["streamingBehavior"] != "steer" {
		t.Fatalf("steer not sent: %v", second)
	}
	piReply(t, p, second, "started")
	p.handle(line(map[string]string{"type": "agent_settled"}))
	mustEnded(t, false, p.ended(), "buffered settlement finished a newly admitted run")
	p.handle(line(map[string]string{"type": "agent_start"}))
	p.handle(line(map[string]string{"type": "agent_settled"}))
	mustEnded(t, true, p.ended(), "admitted run settled")
}

func TestPiInterruptWaitsForAbortAcknowledgement(t *testing.T) {
	s := &sink{}
	p := newPi(s.send)
	p.start("task")
	piReply(t, p, s.last(), "started")
	p.handle(line(map[string]string{"type": "agent_start"}))
	p.steer("unread")
	piReply(t, p, s.last(), "queued")
	p.interrupt("urgent")
	abort := s.last()
	if abort["type"] != "abort" {
		t.Fatalf("expected abort, got %v", abort)
	}
	p.handle(line(map[string]string{"type": "agent_settled"}))
	if s.last()["type"] != "abort" {
		t.Fatal("input was sent before the abort was acknowledged")
	}
	mustEnded(t, false, p.ended(), "aborted run with pending replacement")
	p.handle(line(map[string]any{"type": "response", "id": abort["id"], "success": true}))
	replacement := s.last()
	if replacement["type"] != "prompt" || replacement["message"] != "urgent" {
		t.Fatalf("replacement prompt wrong: %v", replacement)
	}
	piReply(t, p, replacement, "started")
	p.handle(line(map[string]string{"type": "agent_start"}))
	p.handle(line(map[string]string{"type": "agent_settled"}))
	mustEnded(t, true, p.ended(), "replacement run settled")
}
