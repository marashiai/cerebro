package watch

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/marashiai/cerebro/internal/agent"
	"github.com/marashiai/cerebro/internal/jev"
)

// fakeJev answers attention/reason with fixed choices and cites the newest event.
type fakeJev struct {
	mu        sync.Mutex
	attention string
	reason    string
	conf      float64
	status    int
	failures  int    // respond with an error this many times first
	cite      string // cite this event when it is offered, else the newest
	states    []map[string]any
}

func (f *fakeJev) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var body struct {
		State     map[string]any          `json:"state"`
		Questions map[string]jev.Question `json:"questions"`
	}
	json.NewDecoder(r.Body).Decode(&body)
	f.mu.Lock()
	f.states = append(f.states, body.State)
	attention, reason, conf, status, cite := f.attention, f.reason, f.conf, f.status, f.cite
	if f.failures > 0 {
		f.failures--
		status = http.StatusTooManyRequests
	}
	f.mu.Unlock()
	if status != 0 {
		w.WriteHeader(status)
		return
	}
	cited := "none"
	for _, event := range body.State["events"].([]any) {
		if id := event.(map[string]any)["id"].(string); body.Questions["evidence"].Criteria[id] != "" {
			cited = id
		}
	}
	if cite != "" && body.Questions["evidence"].Criteria[cite] != "" {
		cited = cite
	}
	answers := map[string]jev.Answer{}
	for name, question := range body.Questions {
		choice, yes := "no", 0.0
		if name == "evidence" {
			choice = cited
		} else if attention == "concern" && name == reason {
			yes = conf
			if conf >= 0.5 {
				choice = "yes"
			}
		}
		probabilities := map[string]float64{}
		for criterion := range question.Criteria {
			probabilities[criterion] = 0
		}
		if name == "evidence" {
			probabilities[choice] = 1
		} else {
			probabilities["yes"], probabilities["no"] = yes, 1-yes
		}
		answers[name] = jev.Answer{Choice: choice, Confidence: max(yes, 1-yes), Probabilities: probabilities}
	}
	json.NewEncoder(w).Encode(jev.Response{Model: "fake", Answers: answers})
}

type harness struct {
	watcher *Watcher
	fake    *fakeJev
	logs    []map[string]any
	mu      sync.Mutex
}

func start(t *testing.T, attention, reason string, conf float64) *harness {
	t.Helper()
	fake := &fakeJev{attention: attention, reason: reason, conf: conf}
	server := httptest.NewServer(fake)
	t.Cleanup(server.Close)
	client, err := jev.New("key", "fake", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	h := &harness{fake: fake}
	h.watcher, err = New(Config{Client: client, Threshold: 0.8, MaxNudges: 3,
		Request: "Fix the parser only.", Instructions: []string{"AGENTS.md:\nNo compatibility shims."},
		Repository: map[string]any{"branch": "main", "files": []string{"parser.py"}},
		Log:        func(record map[string]any) { h.mu.Lock(); h.logs = append(h.logs, record); h.mu.Unlock() }})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go h.watcher.Run(ctx)
	return h
}

func (h *harness) turn(text string) {
	h.watcher.Observe([]agent.Event{{Kind: "command", Command: text, Exit: new(int)}, {Kind: "turn_end"}})
}

func (h *harness) waitIdle(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if h.watcher.Idle() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("watcher never became idle")
}

func (h *harness) nudge() *Nudge {
	select {
	case nudge := <-h.watcher.Nudges:
		return &nudge
	case <-time.After(300 * time.Millisecond):
		return nil
	}
}

func TestQuietAndWeakConcernsDoNotNudge(t *testing.T) {
	for _, tc := range []struct {
		attention, reason string
		conf              float64
	}{{"clear", "none", 0.99}, {"concern", "scope_creep", 0.5}, {"concern", "none", 0.99}} {
		h := start(t, tc.attention, tc.reason, tc.conf)
		h.turn("cat parser.py")
		h.waitIdle(t)
		if nudge := h.nudge(); nudge != nil {
			t.Fatalf("%+v nudged: %+v", tc, nudge)
		}
	}
}

func TestConcreteConcernNudgesOnceThenEscalatesThenStops(t *testing.T) {
	h := start(t, "concern", "scope_creep", 0.9)
	h.turn("edit billing.py")
	nudge := h.nudge()
	if nudge == nil || nudge.Interrupt || nudge.Reason != "scope_creep" ||
		!strings.Contains(nudge.Text, "edit billing.py") || !strings.HasSuffix(nudge.Text, "continue.") {
		t.Fatalf("first nudge wrong: %+v", nudge)
	}
	h.turn("edit invoices.py")
	if second := h.nudge(); second == nil || !second.Interrupt {
		t.Fatalf("a repeated concern after a nudge should interrupt: %+v", second)
	}
	h.turn("edit receipts.py")
	if third := h.nudge(); third != nil {
		t.Fatalf("a third nudge for the same concern: %+v", third)
	}
	h.waitIdle(t)
	sent := h.fake.states[len(h.fake.states)-1]["nudges_already_sent"].([]any)
	if len(sent) != 2 {
		t.Fatalf("Jev was not told about sent nudges: %v", sent)
	}
}

func TestNudgeLimitAppliesAcrossReasons(t *testing.T) {
	h := start(t, "concern", "scope_creep", 0.9)
	reasons := []string{"scope_creep", "reinvention", "ceremony", "partial_fix"}
	count := 0
	for i, reason := range reasons {
		h.fake.mu.Lock()
		h.fake.reason = reason
		h.fake.mu.Unlock()
		h.turn("step " + reason)
		if h.nudge() != nil {
			count++
		}
		_ = i
	}
	if count != 3 {
		t.Fatalf("nudges = %d, want the limit of 3", count)
	}
}

func TestJevFailureStopsObservationWithoutBlocking(t *testing.T) {
	h := start(t, "clear", "none", 0.9)
	h.fake.status = http.StatusTooManyRequests
	h.turn("go test ./...")
	h.waitIdle(t)
	h.turn("more work")
	if !h.watcher.Idle() {
		t.Fatal("a failed watcher must not hold the run")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	found := false
	for _, record := range h.logs {
		if record["type"] == "jev_failure" {
			found = true
		}
	}
	if !found {
		t.Fatal("Jev failure was not logged")
	}
}

func TestStateCarriesDecisiveContextWithinLimit(t *testing.T) {
	h := start(t, "clear", "none", 0.9)
	h.watcher.UserInput("Actually, only the tokenizer.")
	for i := 0; i < 30; i++ {
		h.watcher.Observe([]agent.Event{{Kind: "file_change", Files: []string{"parser.py"}, Output: strings.Repeat("+x\n", 2000)}})
	}
	h.watcher.Flush()
	h.waitIdle(t)
	for _, state := range h.fake.states {
		data, _ := json.Marshal(state)
		if len(data) > stateLimit+8000 {
			t.Fatalf("state is %d bytes", len(data))
		}
	}
	last := h.fake.states[len(h.fake.states)-1]
	if last["user_request"] != "Fix the parser only." || len(last["later_user_messages"].([]any)) != 1 ||
		last["standing_rules"] == nil || last["repository"].(map[string]any)["branch"] != "main" {
		t.Fatalf("decisive context missing: %v", last)
	}
}

func TestRepeatCitingWorkBeforeTheNudgeDoesNotInterrupt(t *testing.T) {
	h := start(t, "concern", "scope_creep", 0.9)
	h.watcher.Observe([]agent.Event{{Kind: "file_change", Files: []string{"a.py"}}, {Kind: "file_change", Files: []string{"b.py"}}, {Kind: "turn_end"}})
	if first := h.nudge(); first == nil || !strings.Contains(first.Text, "b.py") {
		t.Fatalf("first nudge: %+v", first)
	}
	h.fake.mu.Lock()
	h.fake.cite = "e1" // a.py, edited before the nudge
	h.fake.mu.Unlock()
	h.turn("It is needed because the parser imports it.")
	if old := h.nudge(); old != nil {
		t.Fatalf("work from before the nudge escalated: %+v", old)
	}
	h.fake.mu.Lock()
	h.fake.cite = ""
	h.fake.mu.Unlock()
	h.turn("edit c.py")
	if newer := h.nudge(); newer == nil || !newer.Interrupt {
		t.Fatalf("a newer repeat should interrupt once: %+v", newer)
	}
}

func TestWatcherIsNotIdleWhileANudgeWaits(t *testing.T) {
	h := start(t, "concern", "scope_creep", 0.9)
	h.turn("edit billing.py")
	deadline := time.Now().Add(5 * time.Second)
	for len(h.watcher.Nudges) == 0 && time.Now().Before(deadline) {
		if h.watcher.Idle() {
			t.Fatal("idle before the nudge was handed over")
		}
		time.Sleep(time.Millisecond)
	}
	if h.watcher.Idle() {
		t.Fatal("idle with a nudge waiting to be delivered")
	}
	<-h.watcher.Nudges
	h.waitIdle(t)
}

func TestTransientJevErrorIsRetried(t *testing.T) {
	h := start(t, "concern", "scope_creep", 0.9)
	h.fake.failures = 1
	h.turn("edit billing.py")
	if h.nudge() == nil {
		t.Fatal("one transient error stopped observation")
	}
}

func TestActBeforeAnswerQuotesTheLatestMessage(t *testing.T) {
	h := start(t, "concern", "act_before_answer", 0.9)
	h.watcher.UserInput("What would you change first?")
	h.turn("edit parser.py")
	if nudge := h.nudge(); nudge == nil || !strings.Contains(nudge.Text, "What would you change first?") {
		t.Fatalf("act_before_answer nudge: %+v", nudge)
	}
}
